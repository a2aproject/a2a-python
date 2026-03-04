"""Tests for SnsQueueManager."""

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from a2a.server.events.distributed_event_queue import DistributedEventQueue
from a2a.server.events.event_queue import EventQueue
from a2a.server.events.queue_manager import NoTaskQueue, TaskQueueExists
from a2a.server.events.sns_queue_manager import SnsQueueManager
from a2a.types import Task, TaskState, TaskStatus


TOPIC_ARN = 'arn:aws:sns:us-east-1:123456789012:a2a-events'
QUEUE_URL = 'https://sqs.us-east-1.amazonaws.com/123/a2a-instance-A'

TASK_OBJ = Task(
    id='task-001',
    context_id='ctx-001',
    status=TaskStatus(state=TaskState.submitted),
    kind='task',
)


def _make_sqs_client(messages: list[dict[str, Any]] | None = None) -> AsyncMock:
    client = AsyncMock()
    client.receive_message = AsyncMock(
        return_value={'Messages': messages or []}
    )
    client.delete_message_batch = AsyncMock(return_value={})
    return client


def _make_session(sqs_client: AsyncMock, sns_client: AsyncMock) -> MagicMock:
    session = MagicMock()

    def make_ctx(inner: AsyncMock) -> MagicMock:
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=inner)
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
    client = AsyncMock()
    client.publish = AsyncMock(return_value={})
    return client


@pytest.fixture
def mock_session(sqs_client: AsyncMock, sns_client: AsyncMock) -> MagicMock:
    return _make_session(sqs_client, sns_client)


@pytest.fixture
def manager(mock_session: MagicMock) -> SnsQueueManager:
    return SnsQueueManager(
        topic_arn=TOPIC_ARN,
        sqs_queue_url=QUEUE_URL,
        instance_id='instance-A',
        session=mock_session,
        poll_interval_seconds=0.05,
    )


# ---------------------------------------------------------------------------
# create_or_tap
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_or_tap_creates_distributed_queue(
    manager: SnsQueueManager,
) -> None:
    queue = await manager.create_or_tap('task-001')
    assert isinstance(queue, DistributedEventQueue)


@pytest.mark.asyncio
async def test_create_or_tap_taps_existing_queue(
    manager: SnsQueueManager,
) -> None:
    q1 = await manager.create_or_tap('task-001')
    q2 = await manager.create_or_tap('task-001')
    # Second call should be a tap (child), not the same object.
    assert q1 is not q2
    assert isinstance(q1, DistributedEventQueue)


# ---------------------------------------------------------------------------
# add / get / tap
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_and_get(manager: SnsQueueManager) -> None:
    q = EventQueue()
    await manager.add('task-002', q)
    retrieved = await manager.get('task-002')
    assert retrieved is q


@pytest.mark.asyncio
async def test_add_duplicate_raises(manager: SnsQueueManager) -> None:
    q = EventQueue()
    await manager.add('task-003', q)
    with pytest.raises(TaskQueueExists):
        await manager.add('task-003', q)


@pytest.mark.asyncio
async def test_get_nonexistent_returns_none(manager: SnsQueueManager) -> None:
    result = await manager.get('no-such-task')
    assert result is None


@pytest.mark.asyncio
async def test_tap_nonexistent_returns_none(manager: SnsQueueManager) -> None:
    result = await manager.tap('no-such-task')
    assert result is None


@pytest.mark.asyncio
async def test_tap_creates_child_queue(manager: SnsQueueManager) -> None:
    q = await manager.create_or_tap('task-004')
    child = await manager.tap('task-004')
    assert child is not None
    assert child is not q


# ---------------------------------------------------------------------------
# close
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_close_removes_queue(manager: SnsQueueManager) -> None:
    await manager.create_or_tap('task-005')
    await manager.close('task-005')
    assert await manager.get('task-005') is None


@pytest.mark.asyncio
async def test_close_nonexistent_raises(manager: SnsQueueManager) -> None:
    with pytest.raises(NoTaskQueue):
        await manager.close('no-such-task')


# ---------------------------------------------------------------------------
# SQS polling — event routing
# ---------------------------------------------------------------------------


def _sqs_message(payload: dict[str, Any]) -> dict[str, Any]:
    """Creates a raw-delivery SQS message dict."""
    return {
        'MessageId': 'msg-001',
        'ReceiptHandle': 'rh-001',
        'Body': json.dumps(payload),
    }


@pytest.mark.asyncio
async def test_poll_delivers_event_to_local_queue(
    manager: SnsQueueManager, sqs_client: AsyncMock
) -> None:
    """Events from a remote instance should be delivered via enqueue_local."""
    task_data = json.loads(TASK_OBJ.model_dump_json())
    payload = {
        'instance_id': 'instance-B',  # remote instance
        'task_id': 'task-001',
        'type': 'event',
        'event_kind': 'task',
        'event_data': task_data,
    }
    sqs_client.receive_message = AsyncMock(
        return_value={'Messages': [_sqs_message(payload)]}
    )

    # Register a local queue for task-001.
    dist_queue = DistributedEventQueue(
        publish_fn=AsyncMock(), task_id='task-001', instance_id='instance-A'
    )
    await manager.add('task-001', dist_queue)

    await manager.start()
    # Let the polling loop run at least one cycle.
    await asyncio.sleep(0.15)
    await manager.stop()

    event = await dist_queue.dequeue_event(no_wait=True)
    assert event == TASK_OBJ


@pytest.mark.asyncio
async def test_poll_ignores_own_messages(
    manager: SnsQueueManager, sqs_client: AsyncMock
) -> None:
    """Messages from the same instance_id should be silently discarded."""
    task_data = json.loads(TASK_OBJ.model_dump_json())
    payload = {
        'instance_id': 'instance-A',  # same instance — should be ignored
        'task_id': 'task-001',
        'type': 'event',
        'event_kind': 'task',
        'event_data': task_data,
    }
    sqs_client.receive_message = AsyncMock(
        return_value={'Messages': [_sqs_message(payload)]}
    )

    queue = EventQueue()
    await manager.add('task-001', queue)

    await manager.start()
    await asyncio.sleep(0.15)
    await manager.stop()

    with pytest.raises(asyncio.QueueEmpty):
        queue.queue.get_nowait()


@pytest.mark.asyncio
async def test_poll_close_message_closes_local_queue(
    manager: SnsQueueManager, sqs_client: AsyncMock
) -> None:
    """A 'close' wire message should close the local queue and remove it."""
    payload = {
        'instance_id': 'instance-B',
        'task_id': 'task-001',
        'type': 'close',
    }
    sqs_client.receive_message = AsyncMock(
        return_value={'Messages': [_sqs_message(payload)]}
    )

    dist_queue = DistributedEventQueue(
        publish_fn=AsyncMock(), task_id='task-001', instance_id='instance-A'
    )
    await manager.add('task-001', dist_queue)

    await manager.start()
    await asyncio.sleep(0.15)
    await manager.stop()

    assert dist_queue.is_closed()
    assert await manager.get('task-001') is None


@pytest.mark.asyncio
async def test_poll_drops_message_for_unknown_task(
    manager: SnsQueueManager, sqs_client: AsyncMock
) -> None:
    """Events for tasks without a local queue are silently dropped."""
    payload = {
        'instance_id': 'instance-B',
        'task_id': 'unknown-task',
        'type': 'event',
        'event_kind': 'task',
        'event_data': {},
    }
    sqs_client.receive_message = AsyncMock(
        return_value={'Messages': [_sqs_message(payload)]}
    )

    await manager.start()
    await asyncio.sleep(0.15)
    await manager.stop()
    # No assertion needed — the test passes if no exception is raised.


@pytest.mark.asyncio
async def test_poll_unwraps_sns_notification_envelope(
    manager: SnsQueueManager, sqs_client: AsyncMock
) -> None:
    """SNS notification envelope (non-raw delivery) is properly unwrapped."""
    task_data = json.loads(TASK_OBJ.model_dump_json())
    inner = json.dumps(
        {
            'instance_id': 'instance-B',
            'task_id': 'task-001',
            'type': 'event',
            'event_kind': 'task',
            'event_data': task_data,
        }
    )
    sns_envelope = {
        'Type': 'Notification',
        'MessageId': 'notif-001',
        'TopicArn': TOPIC_ARN,
        'Message': inner,
    }
    sqs_msg = {
        'MessageId': 'msg-002',
        'ReceiptHandle': 'rh-002',
        'Body': json.dumps(sns_envelope),
    }
    sqs_client.receive_message = AsyncMock(return_value={'Messages': [sqs_msg]})

    dist_queue = DistributedEventQueue(
        publish_fn=AsyncMock(), task_id='task-001', instance_id='instance-A'
    )
    await manager.add('task-001', dist_queue)

    await manager.start()
    await asyncio.sleep(0.15)
    await manager.stop()

    event = await dist_queue.dequeue_event(no_wait=True)
    assert event == TASK_OBJ


# ---------------------------------------------------------------------------
# start / stop lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stop_without_start_is_safe(manager: SnsQueueManager) -> None:
    await manager.stop()  # Should not raise.


@pytest.mark.asyncio
async def test_start_stop_lifecycle(
    manager: SnsQueueManager, sqs_client: AsyncMock
) -> None:
    sqs_client.receive_message = AsyncMock(return_value={'Messages': []})
    await manager.start()
    assert manager._poll_task is not None
    assert not manager._poll_task.done()
    await manager.stop()
    assert manager._poll_task is None
