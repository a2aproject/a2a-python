"""Tests for DynamoDBTaskStore."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from a2a.types import Task


MINIMAL_TASK: dict[str, Any] = {
    'id': 'task-ddb-001',
    'context_id': 'session-xyz',
    'status': {'state': 'submitted'},
    'kind': 'task',
}


def _make_mock_session(client_mock: AsyncMock) -> MagicMock:
    """Returns a mock aioboto3.Session whose .client() is a context manager."""
    session = MagicMock()
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client_mock)
    ctx.__aexit__ = AsyncMock(return_value=False)
    session.client.return_value = ctx
    return session


@pytest.fixture
def task() -> Task:
    return Task(**MINIMAL_TASK)


@pytest.fixture
def dynamodb_client() -> AsyncMock:
    client = AsyncMock()
    client.put_item = AsyncMock(return_value={})
    client.delete_item = AsyncMock(return_value={})
    return client


@pytest.fixture
def mock_session(dynamodb_client: AsyncMock) -> MagicMock:
    return _make_mock_session(dynamodb_client)


@pytest.fixture
def store(mock_session: MagicMock):
    with patch.dict('sys.modules', {'aioboto3': MagicMock()}):
        from a2a.server.tasks.dynamodb_task_store import DynamoDBTaskStore

        s = DynamoDBTaskStore('test-table', session=mock_session)
        return s


@pytest.mark.asyncio
async def test_save_calls_put_item(
    store, task: Task, dynamodb_client: AsyncMock
) -> None:
    await store.save(task)
    dynamodb_client.put_item.assert_called_once()
    call_kwargs = dynamodb_client.put_item.call_args.kwargs
    assert call_kwargs['TableName'] == 'test-table'
    assert call_kwargs['Item']['task_id']['S'] == task.id
    assert 'task_data' in call_kwargs['Item']


@pytest.mark.asyncio
async def test_get_returns_task(
    store, task: Task, dynamodb_client: AsyncMock
) -> None:
    task_json = task.model_dump_json()
    dynamodb_client.get_item = AsyncMock(
        return_value={
            'Item': {
                'task_id': {'S': task.id},
                'task_data': {'S': task_json},
            }
        }
    )
    result = await store.get(task.id)
    assert result == task
    dynamodb_client.get_item.assert_called_once()
    call_kwargs = dynamodb_client.get_item.call_args.kwargs
    assert call_kwargs['Key']['task_id']['S'] == task.id
    assert call_kwargs['ConsistentRead'] is True


@pytest.mark.asyncio
async def test_get_nonexistent_returns_none(
    store, dynamodb_client: AsyncMock
) -> None:
    dynamodb_client.get_item = AsyncMock(return_value={})
    result = await store.get('nonexistent-task')
    assert result is None


@pytest.mark.asyncio
async def test_get_empty_item_returns_none(
    store, dynamodb_client: AsyncMock
) -> None:
    dynamodb_client.get_item = AsyncMock(return_value={'Item': None})
    result = await store.get('nonexistent-task')
    assert result is None


@pytest.mark.asyncio
async def test_delete_calls_delete_item(
    store, task: Task, dynamodb_client: AsyncMock
) -> None:
    await store.delete(task.id)
    dynamodb_client.delete_item.assert_called_once()
    call_kwargs = dynamodb_client.delete_item.call_args.kwargs
    assert call_kwargs['TableName'] == 'test-table'
    assert call_kwargs['Key']['task_id']['S'] == task.id


@pytest.mark.asyncio
async def test_delete_nonexistent_is_noop(
    store, dynamodb_client: AsyncMock
) -> None:
    # DynamoDB delete_item is idempotent — no exception expected.
    await store.delete('nonexistent-task')
    dynamodb_client.delete_item.assert_called_once()


@pytest.mark.asyncio
async def test_save_then_get_round_trip(
    mock_session: MagicMock, task: Task
) -> None:
    """Simulate a full round-trip through serialize → deserialize."""
    stored: dict = {}

    async def fake_put_item(**kwargs):
        stored['item'] = kwargs['Item']

    async def fake_get_item(**kwargs):
        if not stored:
            return {}
        return {'Item': stored['item']}

    client = AsyncMock()
    client.put_item = AsyncMock(side_effect=fake_put_item)
    client.get_item = AsyncMock(side_effect=fake_get_item)
    session = _make_mock_session(client)

    with patch.dict('sys.modules', {'aioboto3': MagicMock()}):
        from a2a.server.tasks.dynamodb_task_store import DynamoDBTaskStore

        s = DynamoDBTaskStore('test-table', session=session)

    await s.save(task)
    retrieved = await s.get(task.id)
    assert retrieved == task


def test_import_error_without_aioboto3() -> None:
    """DynamoDBTaskStore raises ImportError when aioboto3 is missing."""
    import sys

    original = sys.modules.pop('aioboto3', None)
    try:
        # Remove the cached module so the import attempt fails.
        sys.modules['aioboto3'] = None  # type: ignore[assignment]
        from importlib import reload
        import a2a.server.tasks.dynamodb_task_store as mod

        reload(mod)
        with pytest.raises(ImportError, match='aws extra'):
            mod.DynamoDBTaskStore('test-table')
    finally:
        if original is not None:
            sys.modules['aioboto3'] = original
        else:
            sys.modules.pop('aioboto3', None)
