"""Tests for DistributedEventQueue."""

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from a2a.server.events.distributed_event_queue import (
    DistributedEventQueue,
    _CLOSE_TYPE,
    _EVENT_TYPE,
    decode_event,
    deserialise_wire_message,
)
from a2a.types import Task, TaskState, TaskStatus


TASK_OBJ = Task(
    id='task-001',
    context_id='ctx-001',
    status=TaskStatus(state=TaskState.submitted),
    kind='task',
)


@pytest.fixture
def publish_fn() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def queue(publish_fn: AsyncMock) -> DistributedEventQueue:
    return DistributedEventQueue(
        publish_fn=publish_fn,
        task_id='task-001',
        instance_id='instance-A',
    )


# ---------------------------------------------------------------------------
# enqueue_event — local + SNS publish
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enqueue_event_delivers_locally(
    queue: DistributedEventQueue, publish_fn: AsyncMock
) -> None:
    await queue.enqueue_event(TASK_OBJ)
    # Give the fire-and-forget task a chance to run.
    await asyncio.sleep(0)
    event = await queue.dequeue_event(no_wait=True)
    assert event == TASK_OBJ


@pytest.mark.asyncio
async def test_enqueue_event_publishes_to_sns(
    queue: DistributedEventQueue, publish_fn: AsyncMock
) -> None:
    await queue.enqueue_event(TASK_OBJ)
    await asyncio.sleep(0)
    publish_fn.assert_called_once()
    raw: str = publish_fn.call_args.args[0]
    payload = json.loads(raw)
    assert payload['type'] == _EVENT_TYPE
    assert payload['task_id'] == 'task-001'
    assert payload['instance_id'] == 'instance-A'
    assert payload['event_kind'] == 'task'


# ---------------------------------------------------------------------------
# enqueue_local — local only, no SNS
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enqueue_local_delivers_locally(
    queue: DistributedEventQueue, publish_fn: AsyncMock
) -> None:
    await queue.enqueue_local(TASK_OBJ)
    await asyncio.sleep(0)
    event = await queue.dequeue_event(no_wait=True)
    assert event == TASK_OBJ


@pytest.mark.asyncio
async def test_enqueue_local_does_not_publish_to_sns(
    queue: DistributedEventQueue, publish_fn: AsyncMock
) -> None:
    await queue.enqueue_local(TASK_OBJ)
    await asyncio.sleep(0)
    publish_fn.assert_not_called()


# ---------------------------------------------------------------------------
# close — publishes close signal
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_close_publishes_close_signal(
    queue: DistributedEventQueue, publish_fn: AsyncMock
) -> None:
    await queue.close(immediate=True)
    await asyncio.sleep(0)
    publish_fn.assert_called_once()
    raw: str = publish_fn.call_args.args[0]
    payload = json.loads(raw)
    assert payload['type'] == _CLOSE_TYPE
    assert payload['task_id'] == 'task-001'
    assert payload['instance_id'] == 'instance-A'


@pytest.mark.asyncio
async def test_close_does_not_publish_twice(
    queue: DistributedEventQueue, publish_fn: AsyncMock
) -> None:
    await queue.close(immediate=True)
    await asyncio.sleep(0)
    await queue.close(immediate=True)
    await asyncio.sleep(0)
    # Second close should not trigger another publish.
    assert publish_fn.call_count == 1


# ---------------------------------------------------------------------------
# Wire format helpers
# ---------------------------------------------------------------------------


def test_deserialise_wire_message_valid() -> None:
    raw = json.dumps({'type': 'event', 'task_id': 'x', 'instance_id': 'A'})
    msg = deserialise_wire_message(raw)
    assert msg['type'] == 'event'


def test_deserialise_wire_message_missing_type() -> None:
    raw = json.dumps({'task_id': 'x'})
    with pytest.raises(ValueError, match="missing 'type'"):
        deserialise_wire_message(raw)


def test_deserialise_wire_message_invalid_json() -> None:
    with pytest.raises(ValueError, match='Malformed'):
        deserialise_wire_message('{not-json')


def test_decode_event_known_kind() -> None:
    task_dict = json.loads(TASK_OBJ.model_dump_json())
    msg: dict[str, Any] = {
        'event_kind': 'task',
        'event_data': task_dict,
    }
    event = decode_event(msg)
    assert isinstance(event, Task)
    assert event.id == TASK_OBJ.id


def test_decode_event_unknown_kind_returns_none() -> None:
    msg: dict[str, Any] = {
        'event_kind': 'unknown-kind',
        'event_data': {},
    }
    assert decode_event(msg) is None


def test_decode_event_missing_fields_returns_none() -> None:
    assert decode_event({}) is None
