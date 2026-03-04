"""Tests for QueueLifecycleManager."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from a2a.server.events.queue_lifecycle_manager import (
    QueueLifecycleManager,
    QueueProvisionResult,
)


TOPIC_ARN = 'arn:aws:sns:us-east-1:123456789012:a2a-events'
QUEUE_URL = 'https://sqs.us-east-1.amazonaws.com/123456789012/a2a-instance-uuid'
QUEUE_ARN = 'arn:aws:sqs:us-east-1:123456789012:a2a-instance-uuid'
SUBSCRIPTION_ARN = 'arn:aws:sns:us-east-1:123456789012:a2a-events:sub-uuid'


def _make_sqs_client() -> AsyncMock:
    client = AsyncMock()
    client.create_queue = AsyncMock(return_value={'QueueUrl': QUEUE_URL})
    client.get_queue_attributes = AsyncMock(
        return_value={'Attributes': {'QueueArn': QUEUE_ARN}}
    )
    client.set_queue_attributes = AsyncMock(return_value={})
    client.delete_queue = AsyncMock(return_value={})
    return client


def _make_sns_client() -> AsyncMock:
    client = AsyncMock()
    client.subscribe = AsyncMock(
        return_value={'SubscriptionArn': SUBSCRIPTION_ARN}
    )
    client.unsubscribe = AsyncMock(return_value={})
    return client


def _make_session(sqs_client: AsyncMock, sns_client: AsyncMock) -> MagicMock:
    session = MagicMock()

    def make_ctx(inner_client: AsyncMock) -> MagicMock:
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=inner_client)
        ctx.__aexit__ = AsyncMock(return_value=False)
        return ctx

    def client_factory(service: str, **kwargs):
        if service == 'sqs':
            return make_ctx(sqs_client)
        return make_ctx(sns_client)

    session.client.side_effect = client_factory
    return session


@pytest.fixture
def sqs_client() -> AsyncMock:
    return _make_sqs_client()


@pytest.fixture
def sns_client() -> AsyncMock:
    return _make_sns_client()


@pytest.fixture
def mock_session(sqs_client: AsyncMock, sns_client: AsyncMock) -> MagicMock:
    return _make_session(sqs_client, sns_client)


@pytest.fixture
def manager(mock_session: MagicMock) -> QueueLifecycleManager:
    return QueueLifecycleManager(
        topic_arn=TOPIC_ARN,
        instance_id='test-instance',
        session=mock_session,
    )


# ---------------------------------------------------------------------------
# provision()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_provision_creates_sqs_queue(
    manager: QueueLifecycleManager, sqs_client: AsyncMock
) -> None:
    result = await manager.provision()
    sqs_client.create_queue.assert_called_once()
    assert result.queue_url == QUEUE_URL


@pytest.mark.asyncio
async def test_provision_fetches_queue_arn(
    manager: QueueLifecycleManager, sqs_client: AsyncMock
) -> None:
    result = await manager.provision()
    sqs_client.get_queue_attributes.assert_called_once()
    assert result.queue_arn == QUEUE_ARN


@pytest.mark.asyncio
async def test_provision_subscribes_to_sns(
    manager: QueueLifecycleManager, sns_client: AsyncMock
) -> None:
    result = await manager.provision()
    sns_client.subscribe.assert_called_once()
    call_kwargs = sns_client.subscribe.call_args.kwargs
    assert call_kwargs['TopicArn'] == TOPIC_ARN
    assert call_kwargs['Protocol'] == 'sqs'
    assert result.subscription_arn == SUBSCRIPTION_ARN


@pytest.mark.asyncio
async def test_provision_sets_queue_policy(
    manager: QueueLifecycleManager, sqs_client: AsyncMock
) -> None:
    await manager.provision()
    sqs_client.set_queue_attributes.assert_called_once()
    call_kwargs = sqs_client.set_queue_attributes.call_args.kwargs
    assert 'Policy' in call_kwargs['Attributes']


@pytest.mark.asyncio
async def test_provision_is_idempotent(
    manager: QueueLifecycleManager, sqs_client: AsyncMock
) -> None:
    r1 = await manager.provision()
    r2 = await manager.provision()
    assert r1 == r2
    # create_queue should only be called once.
    assert sqs_client.create_queue.call_count == 1


@pytest.mark.asyncio
async def test_provision_sets_properties(
    manager: QueueLifecycleManager,
) -> None:
    assert manager.queue_url is None
    assert manager.queue_arn is None
    assert manager.subscription_arn is None
    await manager.provision()
    assert manager.queue_url == QUEUE_URL
    assert manager.queue_arn == QUEUE_ARN
    assert manager.subscription_arn == SUBSCRIPTION_ARN


@pytest.mark.asyncio
async def test_provision_rollback_on_subscribe_failure(
    manager: QueueLifecycleManager,
    sqs_client: AsyncMock,
    sns_client: AsyncMock,
) -> None:
    sns_client.subscribe = AsyncMock(side_effect=RuntimeError('SNS error'))
    with pytest.raises(RuntimeError, match='SNS error'):
        await manager.provision()
    # Queue should have been deleted as a rollback.
    sqs_client.delete_queue.assert_called_once()
    # Properties should be None after rollback.
    assert manager.queue_url is None


# ---------------------------------------------------------------------------
# teardown()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_teardown_unsubscribes_and_deletes(
    manager: QueueLifecycleManager,
    sqs_client: AsyncMock,
    sns_client: AsyncMock,
) -> None:
    await manager.provision()
    await manager.teardown()
    sns_client.unsubscribe.assert_called_once_with(
        SubscriptionArn=SUBSCRIPTION_ARN
    )
    sqs_client.delete_queue.assert_called_once_with(QueueUrl=QUEUE_URL)


@pytest.mark.asyncio
async def test_teardown_before_provision_is_noop(
    manager: QueueLifecycleManager,
    sqs_client: AsyncMock,
    sns_client: AsyncMock,
) -> None:
    await manager.teardown()
    sns_client.unsubscribe.assert_not_called()
    sqs_client.delete_queue.assert_not_called()


@pytest.mark.asyncio
async def test_teardown_clears_properties(
    manager: QueueLifecycleManager,
) -> None:
    await manager.provision()
    assert manager.queue_url is not None
    await manager.teardown()
    assert manager.queue_url is None
    assert manager.subscription_arn is None


@pytest.mark.asyncio
async def test_teardown_continues_after_unsubscribe_failure(
    manager: QueueLifecycleManager,
    sqs_client: AsyncMock,
    sns_client: AsyncMock,
) -> None:
    sns_client.unsubscribe = AsyncMock(side_effect=RuntimeError('SNS error'))
    await manager.provision()
    # Should not raise — teardown is best-effort.
    await manager.teardown()
    # delete_queue should still be called.
    sqs_client.delete_queue.assert_called_once()


# ---------------------------------------------------------------------------
# Async context manager
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_context_manager(
    mock_session: MagicMock,
    sqs_client: AsyncMock,
    sns_client: AsyncMock,
) -> None:
    async with QueueLifecycleManager(
        topic_arn=TOPIC_ARN, session=mock_session
    ) as mgr:
        assert mgr.queue_url == QUEUE_URL
    # After exit, teardown should have been called.
    sns_client.unsubscribe.assert_called_once()
    sqs_client.delete_queue.assert_called_once()
