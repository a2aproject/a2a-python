"""Integration tests for the distributed A2A stack (DynamoDB + SNS/SQS)."""

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from a2a.server.events.distributed_event_queue import DistributedEventQueue
from a2a.server.events.queue_lifecycle_manager import QueueLifecycleManager
from a2a.server.events.sns_queue_manager import SnsQueueManager
from a2a.server.tasks.dynamodb_task_store import DynamoDBTaskStore
from a2a.types import Task, TaskState, TaskStatus


TOPIC_ARN = 'arn:aws:sns:us-east-1:123456789012:a2a-events'
QUEUE_URL_A = 'https://sqs.us-east-1.amazonaws.com/123/a2a-instance-A'
QUEUE_URL_B = 'https://sqs.us-east-1.amazonaws.com/123/a2a-instance-B'
QUEUE_ARN = 'arn:aws:sqs:us-east-1:123456789012:a2a-instance-A'
SUBSCRIPTION_ARN = 'arn:aws:sns:us-east-1:123456789012:a2a-events:sub-001'

TASK_OBJ = Task(
    id='integration-task-001',
    context_id='ctx-integration',
    status=TaskStatus(state=TaskState.submitted),
    kind='task',
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ddb_session(task: Task) -> MagicMock:
    """Mock DynamoDB session that stores tasks in a dict."""
    store: dict[str, str] = {}

    async def fake_put(**kwargs):
        item = kwargs['Item']
        store[item['task_id']['S']] = item['task_data']['S']

    async def fake_get(**kwargs):
        task_id = kwargs['Key']['task_id']['S']
        data = store.get(task_id)
        if not data:
            return {}
        return {'Item': {'task_id': {'S': task_id}, 'task_data': {'S': data}}}

    async def fake_delete(**kwargs):
        task_id = kwargs['Key']['task_id']['S']
        store.pop(task_id, None)

    client = AsyncMock()
    client.put_item = AsyncMock(side_effect=fake_put)
    client.get_item = AsyncMock(side_effect=fake_get)
    client.delete_item = AsyncMock(side_effect=fake_delete)

    session = MagicMock()
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=False)
    session.client.return_value = ctx
    return session


def _make_sqs_session(
    sqs_client_a: AsyncMock,
    sqs_client_b: AsyncMock,
    sns_client: AsyncMock,
) -> tuple[MagicMock, MagicMock]:
    """Returns (session_a, session_b) with separate SQS clients."""

    def _make(sqs: AsyncMock) -> MagicMock:
        session = MagicMock()

        def make_ctx(inner: AsyncMock) -> MagicMock:
            ctx = MagicMock()
            ctx.__aenter__ = AsyncMock(return_value=inner)
            ctx.__aexit__ = AsyncMock(return_value=False)
            return ctx

        def factory(service: str, **kwargs):
            return make_ctx(sqs if service == 'sqs' else sns_client)

        session.client.side_effect = factory
        return session

    return _make(sqs_client_a), _make(sqs_client_b)


def _wire_event(
    instance_id: str, task_id: str, event: Task
) -> dict[str, Any]:
    task_data = json.loads(event.model_dump_json())
    return {
        'instance_id': instance_id,
        'task_id': task_id,
        'type': 'event',
        'event_kind': 'task',
        'event_data': task_data,
    }


# ---------------------------------------------------------------------------
# Integration: DynamoDB store round-trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dynamodb_store_full_round_trip() -> None:
    """Persist, retrieve, and delete a task via DynamoDBTaskStore."""
    session = _make_ddb_session(TASK_OBJ)
    store = DynamoDBTaskStore('integration-table', session=session)

    await store.save(TASK_OBJ)
    retrieved = await store.get(TASK_OBJ.id)
    assert retrieved == TASK_OBJ

    await store.delete(TASK_OBJ.id)
    deleted = await store.get(TASK_OBJ.id)
    assert deleted is None


@pytest.mark.asyncio
async def test_dynamodb_store_overwrite() -> None:
    """Saving the same task twice should overwrite without error."""
    session = _make_ddb_session(TASK_OBJ)
    store = DynamoDBTaskStore('integration-table', session=session)

    await store.save(TASK_OBJ)

    updated = TASK_OBJ.model_copy(
        update={'status': TaskStatus(state=TaskState.completed)}
    )
    await store.save(updated)

    retrieved = await store.get(TASK_OBJ.id)
    assert retrieved is not None
    assert retrieved.status.state == TaskState.completed


# ---------------------------------------------------------------------------
# Integration: multi-instance fan-out
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_two_instance_event_fan_out() -> None:
    """Events published on instance A must be delivered on instance B."""

    # Shared in-memory SNS "bus" — collects published messages.
    published_messages: list[str] = []

    async def shared_sns_publish(**kwargs):
        published_messages.append(kwargs['Message'])

    sns_client = AsyncMock()
    sns_client.publish = AsyncMock(side_effect=shared_sns_publish)

    # SQS client B: delivers whatever instance A published into B's queue.
    messages_for_b: list[dict[str, Any]] = []

    async def sqs_b_receive(**kwargs):
        msgs = [
            {
                'MessageId': f'msg-{i}',
                'ReceiptHandle': f'rh-{i}',
                'Body': m,
            }
            for i, m in enumerate(messages_for_b)
        ]
        messages_for_b.clear()
        return {'Messages': msgs}

    sqs_client_a = AsyncMock()
    sqs_client_a.receive_message = AsyncMock(return_value={'Messages': []})
    sqs_client_a.delete_message_batch = AsyncMock(return_value={})

    sqs_client_b = AsyncMock()
    sqs_client_b.receive_message = AsyncMock(side_effect=sqs_b_receive)
    sqs_client_b.delete_message_batch = AsyncMock(return_value={})

    session_a, session_b = _make_sqs_session(
        sqs_client_a, sqs_client_b, sns_client
    )

    manager_a = SnsQueueManager(
        topic_arn=TOPIC_ARN,
        sqs_queue_url=QUEUE_URL_A,
        instance_id='instance-A',
        session=session_a,
        poll_interval_seconds=0.05,
    )
    manager_b = SnsQueueManager(
        topic_arn=TOPIC_ARN,
        sqs_queue_url=QUEUE_URL_B,
        instance_id='instance-B',
        session=session_b,
        poll_interval_seconds=0.05,
    )

    # Create a local queue on instance B for the task.
    queue_b = DistributedEventQueue(
        publish_fn=AsyncMock(),
        task_id=TASK_OBJ.id,
        instance_id='instance-B',
    )
    await manager_b.add(TASK_OBJ.id, queue_b)

    await manager_a.start()
    await manager_b.start()

    # Instance A: create a distributed queue and enqueue an event.
    queue_a = await manager_a.create_or_tap(TASK_OBJ.id)
    await queue_a.enqueue_event(TASK_OBJ)

    # Give the fire-and-forget SNS publish a moment to complete.
    await asyncio.sleep(0.05)

    # Simulate SNS → SQS delivery: move published messages to B's inbox.
    messages_for_b.extend(published_messages)
    published_messages.clear()

    # Let B's poller deliver the event.
    await asyncio.sleep(0.2)

    await manager_a.stop()
    await manager_b.stop()

    # Verify instance B received the event.
    event = await queue_b.dequeue_event(no_wait=True)
    assert event == TASK_OBJ


# ---------------------------------------------------------------------------
# Integration: QueueLifecycleManager + SnsQueueManager wiring
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lifecycle_manager_provisions_for_sns_queue_manager() -> None:
    """Provision result from QueueLifecycleManager feeds into SnsQueueManager."""
    sqs_client = AsyncMock()
    sqs_client.create_queue = AsyncMock(return_value={'QueueUrl': QUEUE_URL_A})
    sqs_client.get_queue_attributes = AsyncMock(
        return_value={'Attributes': {'QueueArn': QUEUE_ARN}}
    )
    sqs_client.set_queue_attributes = AsyncMock(return_value={})
    sqs_client.delete_queue = AsyncMock(return_value={})
    sqs_client.receive_message = AsyncMock(return_value={'Messages': []})
    sqs_client.delete_message_batch = AsyncMock(return_value={})

    sns_client = AsyncMock()
    sns_client.subscribe = AsyncMock(
        return_value={'SubscriptionArn': SUBSCRIPTION_ARN}
    )
    sns_client.unsubscribe = AsyncMock(return_value={})
    sns_client.publish = AsyncMock(return_value={})

    def _make_session_combined() -> MagicMock:
        session = MagicMock()

        def make_ctx(inner: AsyncMock) -> MagicMock:
            ctx = MagicMock()
            ctx.__aenter__ = AsyncMock(return_value=inner)
            ctx.__aexit__ = AsyncMock(return_value=False)
            return ctx

        def factory(service: str, **kwargs):
            if service == 'sqs':
                return make_ctx(sqs_client)
            return make_ctx(sns_client)

        session.client.side_effect = factory
        return session

    session = _make_session_combined()

    async with QueueLifecycleManager(
        topic_arn=TOPIC_ARN, session=session
    ) as lcm:
        assert lcm.queue_url == QUEUE_URL_A
        manager = SnsQueueManager(
            topic_arn=TOPIC_ARN,
            sqs_queue_url=lcm.queue_url,
            instance_id=lcm.instance_id,
            session=session,
            poll_interval_seconds=0.05,
        )
        await manager.start()
        queue = await manager.create_or_tap('task-wired')
        assert isinstance(queue, DistributedEventQueue)
        await manager.stop()

    sns_client.unsubscribe.assert_called_once()
    sqs_client.delete_queue.assert_called_once()
