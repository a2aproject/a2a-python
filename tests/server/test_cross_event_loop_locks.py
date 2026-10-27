"""Regression tests: in-memory server singletons are safe across event loops."""

import asyncio
import threading

from unittest.mock import MagicMock

import pytest

from a2a.server.agent_execution.active_task_registry import ActiveTaskRegistry
from a2a.server.events.in_memory_queue_manager import InMemoryQueueManager
from a2a.server.request_handlers.default_request_handler import (
    LegacyRequestHandler,
)
from a2a.server.tasks.inmemory_push_notification_config_store import (
    InMemoryPushNotificationConfigStore,
)
from a2a.server.tasks.inmemory_task_store import _InMemoryTaskStoreImpl


_RLOCK_TYPE = type(threading.RLock())


class _PersistentLoop:
    """An asyncio event loop running forever on its own daemon thread."""

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def run(self, coro, timeout: float = 10):
        """Runs coro to completion on this loop and returns its result."""
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(
            timeout=timeout
        )

    def close(self) -> None:
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=10)


@pytest.fixture
def two_loops():
    loop_a = _PersistentLoop()
    loop_b = _PersistentLoop()
    try:
        yield loop_a, loop_b
    finally:
        loop_a.close()
        loop_b.close()


# --- deterministic invariant: the guard is a threading lock, not asyncio ---


def test_queue_manager_lock_is_threading_lock() -> None:
    manager = InMemoryQueueManager()
    assert not isinstance(manager._lock, asyncio.Lock)
    assert isinstance(manager._lock, _RLOCK_TYPE)


def test_active_task_registry_lock_is_threading_lock() -> None:
    registry = ActiveTaskRegistry(
        agent_executor=MagicMock(), task_store=MagicMock()
    )
    assert not isinstance(registry._lock, asyncio.Lock)
    assert isinstance(registry._lock, _RLOCK_TYPE)


def test_inmemory_task_store_lock_is_threading_lock() -> None:
    store = _InMemoryTaskStoreImpl()
    assert not isinstance(store.lock, asyncio.Lock)
    assert isinstance(store.lock, _RLOCK_TYPE)


def test_push_config_store_lock_is_threading_lock() -> None:
    store = InMemoryPushNotificationConfigStore()
    assert not isinstance(store.lock, asyncio.Lock)
    assert isinstance(store.lock, _RLOCK_TYPE)


def test_request_handler_running_agents_lock_is_threading_lock() -> None:
    handler = LegacyRequestHandler(MagicMock(), MagicMock(), MagicMock())
    assert not isinstance(handler._running_agents_lock, asyncio.Lock)
    assert isinstance(handler._running_agents_lock, _RLOCK_TYPE)


# --- functional: the real objects work when driven from two event loops ---


def test_queue_manager_across_event_loops(two_loops) -> None:
    loop_a, loop_b = two_loops
    manager = InMemoryQueueManager()

    # Task 1 is created on loop A (the first loop to touch the manager's guard).
    loop_a.run(manager.create_or_tap('task-1'))

    # Task 2 is created on loop B, and task 1 is tapped/closed from loop B --
    # exercising the shared guard from a different loop than created it.
    assert loop_b.run(manager.create_or_tap('task-2')) is not None
    assert loop_b.run(manager.tap('task-1')) is not None
    assert loop_a.run(manager.get('task-2')) is not None
    loop_b.run(manager.close('task-1'))
    assert loop_a.run(manager.get('task-1')) is None


def test_push_config_store_across_event_loops(two_loops) -> None:
    loop_a, loop_b = two_loops
    store = InMemoryPushNotificationConfigStore()

    # get_info_for_dispatch takes no ServerCallContext, so it exercises the
    # guard directly from each loop.
    assert loop_a.run(store.get_info_for_dispatch('task-1')) == []
    assert loop_b.run(store.get_info_for_dispatch('task-1')) == []


def test_queue_manager_interleaved_across_loops(two_loops) -> None:
    loop_a, loop_b = two_loops
    manager = InMemoryQueueManager()
    for i in range(10):
        loop = loop_a if i % 2 == 0 else loop_b
        loop.run(manager.create_or_tap(f'task-{i}'))
    for i in range(10):
        # Read each task from the opposite loop that created it.
        loop = loop_b if i % 2 == 0 else loop_a
        assert loop.run(manager.get(f'task-{i}')) is not None


# --- contended acquisition across loops (deterministic; the discriminator) ---


def _contended_acquire(lock, two_loops) -> None:
    """Holds ``lock`` on loop A while loop B acquires it (a genuinely contended
    cross-loop acquisition). With an ``asyncio.Lock`` this raises
    ``RuntimeError: bound to a different event loop`` (or deadlocks); with a
    ``threading.RLock`` it serializes and completes."""
    loop_a, loop_b = two_loops
    a_holds = threading.Event()
    b_queued = threading.Event()

    async def holder() -> None:
        lock.acquire()
        try:
            a_holds.set()
            b_queued.wait(3)
        finally:
            lock.release()

    async def contender() -> None:
        a_holds.wait(3)
        b_queued.set()
        # Acquire on loop B's thread without freezing the loop.
        await asyncio.get_event_loop().run_in_executor(
            None, _acquire_release, lock
        )

    fut_a = asyncio.run_coroutine_threadsafe(holder(), loop_a._loop)
    fut_b = asyncio.run_coroutine_threadsafe(contender(), loop_b._loop)
    fut_b.result(timeout=5)
    fut_a.result(timeout=5)


def _acquire_release(lock) -> None:
    lock.acquire()
    lock.release()


def test_queue_manager_lock_contended_across_loops(two_loops) -> None:
    _contended_acquire(InMemoryQueueManager()._lock, two_loops)


def test_task_store_lock_contended_across_loops(two_loops) -> None:
    _contended_acquire(_InMemoryTaskStoreImpl().lock, two_loops)


def test_active_task_registry_lock_contended_across_loops(two_loops) -> None:
    registry = ActiveTaskRegistry(
        agent_executor=MagicMock(), task_store=MagicMock()
    )
    _contended_acquire(registry._lock, two_loops)


# --- end-to-end: on_message_send driven from two loops through v2 handler ---


def test_on_message_send_across_event_loops(two_loops) -> None:
    """Drive the real request handler's ``on_message_send`` for two task ids
    concurrently on two event loops sharing one handler + one task store."""
    import uuid

    from a2a.auth.user import UnauthenticatedUser
    from a2a.helpers.proto_helpers import new_task_from_user_message
    from a2a.server.agent_execution import AgentExecutor
    from a2a.server.context import ServerCallContext
    from a2a.server.request_handlers.default_request_handler_v2 import (
        DefaultRequestHandlerV2,
    )
    from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
    from a2a.types import (
        AgentCapabilities,
        AgentCard,
        Message,
        Part,
        Role,
        SendMessageRequest,
        TaskState,
    )

    class _CompletingAgent(AgentExecutor):
        async def execute(self, context, event_queue) -> None:
            if context.message:
                await event_queue.enqueue_event(
                    new_task_from_user_message(context.message)
                )
            updater = TaskUpdater(
                event_queue,
                task_id=context.task_id or str(uuid.uuid4()),
                context_id=context.context_id or str(uuid.uuid4()),
            )
            await updater.update_status(TaskState.TASK_STATE_WORKING)
            await updater.complete()

        async def cancel(self, context, event_queue) -> None:
            raise NotImplementedError

    loop_a, loop_b = two_loops
    handler = DefaultRequestHandlerV2(
        _CompletingAgent(),
        InMemoryTaskStore(),
        AgentCard(
            name='test_agent',
            version='1.0',
            capabilities=AgentCapabilities(streaming=True),
        ),
    )
    ctx = ServerCallContext(user=UnauthenticatedUser())

    def req(mid: str) -> SendMessageRequest:
        return SendMessageRequest(
            message=Message(
                role=Role.ROLE_USER, message_id=mid, parts=[Part(text='hi')]
            )
        )

    # Two requests genuinely in-flight on two loops against the same handler.
    fut_a = asyncio.run_coroutine_threadsafe(
        handler.on_message_send(req('a1'), ctx), loop_a._loop
    )
    fut_b = asyncio.run_coroutine_threadsafe(
        handler.on_message_send(req('b1'), ctx), loop_b._loop
    )
    task_a = fut_a.result(timeout=15)
    task_b = fut_b.result(timeout=15)
    assert task_a.status.state == TaskState.TASK_STATE_COMPLETED
    assert task_b.status.state == TaskState.TASK_STATE_COMPLETED
