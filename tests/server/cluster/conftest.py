import asyncio
import contextlib
import threading

from collections.abc import AsyncGenerator

import pytest

from a2a.auth.user import User
from a2a.helpers.proto_helpers import new_task_from_user_message
from a2a.server.agent_execution.agent_executor import AgentExecutor
from a2a.server.cluster import (
    ConcurrentTaskModificationError,
    StoredTask,
    TaskEventStream,
    TaskVersion,
    VersionedEvent,
    VersionedTaskStore,
)
from a2a.server.context import ServerCallContext
from a2a.server.events.event_queue import Event
from a2a.server.owner_resolver import OwnerResolver, resolve_user_scope
from a2a.server.request_handlers.default_request_handler_v2 import (
    DefaultRequestHandlerV2,
)
from a2a.server.tasks.inmemory_task_store import InMemoryTaskStore
from a2a.server.tasks.task_updater import TaskUpdater
from a2a.types.a2a_pb2 import (
    AgentCapabilities,
    AgentCard,
    ListTasksRequest,
    ListTasksResponse,
    Message,
    Part,
    Role,
    SendMessageRequest,
    Task,
    TaskState,
)


# --- In-memory cluster doubles ----------------------------------------------

_TERMINAL_STATES = frozenset(
    {
        TaskState.TASK_STATE_COMPLETED,
        TaskState.TASK_STATE_CANCELED,
        TaskState.TASK_STATE_FAILED,
        TaskState.TASK_STATE_REJECTED,
    }
)


class VersionedInMemoryTaskStore(VersionedTaskStore):
    """`VersionedTaskStore` backed by in-process dictionaries.

    Holds one integer version per (owner, task_id). `save` performs a
    compare-and-swap against `prev_version` and raises
    `ConcurrentTaskModificationError` on mismatch. Reads and list operations
    delegate to a wrapped `InMemoryTaskStore` for the task data itself.
    """

    def __init__(
        self, owner_resolver: OwnerResolver = resolve_user_scope
    ) -> None:
        self._owner_resolver = owner_resolver
        self._store = InMemoryTaskStore()
        # Maps owner to a mapping of task_id to its current integer version.
        self._versions: dict[str, dict[str, int]] = {}
        self._lock = threading.RLock()

    def _current_version_locked(self, owner: str, task_id: str) -> int:
        return self._versions.get(owner, {}).get(task_id, 0)

    async def save(
        self,
        task: Task,
        *,
        event: Event | None,
        prev: Task | None,
        prev_version: TaskVersion,
        context: ServerCallContext,
    ) -> TaskVersion:
        """Persists `task`, bumping its version, with a compare-and-swap.

        Raises `ConcurrentTaskModificationError` if `prev_version` does not
        match the currently stored version. `TaskVersion.MISSING` skips the
        check; a write moving `task` to CANCELED overwrites a non-terminal
        stored task without a version check, and raises when it is terminal
        or absent.
        """
        del event, prev  # in-memory store keeps no event log
        owner = self._owner_resolver(context)
        with self._lock:
            stored = self._current_version_locked(owner, task_id=task.id)
            if task.status.state == TaskState.TASK_STATE_CANCELED:
                current = await self._store.get(task.id, context)
                if current is None or current.status.state in _TERMINAL_STATES:
                    raise ConcurrentTaskModificationError(task.id)
            elif not prev_version.is_missing:
                if stored == 0:
                    raise ConcurrentTaskModificationError(task.id)
                if TaskVersion(stored) != prev_version:
                    raise ConcurrentTaskModificationError(task.id)
            new_version = stored + 1
            await self._store.save(task, context)
            self._versions.setdefault(owner, {})[task.id] = new_version
            return TaskVersion(new_version)

    async def get(
        self, task_id: str, context: ServerCallContext
    ) -> StoredTask | None:
        """Returns the task with its current version, or None if absent."""
        owner = self._owner_resolver(context)
        with self._lock:
            task = await self._store.get(task_id, context)
            if task is None:
                return None
            stored = self._current_version_locked(owner, task_id)
            return StoredTask(task, TaskVersion(stored))

    async def list(
        self,
        params: ListTasksRequest,
        context: ServerCallContext,
    ) -> ListTasksResponse:
        """Lists tasks via the wrapped in-memory store."""
        return await self._store.list(params, context)

    async def delete(self, task_id: str, context: ServerCallContext) -> None:
        """Deletes a task and forgets its version."""
        owner = self._owner_resolver(context)
        with self._lock:
            await self._store.delete(task_id, context)
            owner_versions = self._versions.get(owner)
            if owner_versions is not None:
                owner_versions.pop(task_id, None)
                if not owner_versions:
                    del self._versions[owner]


class InMemoryTaskEventStream(TaskEventStream):
    """`TaskEventStream` that fans out to in-process subscribers.

    `publish` delivers to every current subscriber of the task; `subscribe`
    returns an async iterator backed by a per-subscriber queue and filters on
    the caller's `after` version.
    """

    def __init__(self) -> None:
        # {task_id: set of subscriber queues}
        self._subscribers: dict[str, set[asyncio.Queue[VersionedEvent]]] = {}
        self._lock = asyncio.Lock()

    async def publish(self, task_id: str, event: VersionedEvent) -> None:
        """Delivers `event` to all current subscribers of `task_id`."""
        async with self._lock:
            queues = list(self._subscribers.get(task_id, ()))
        for queue in queues:
            await queue.put(event)

    async def subscribe(  # type: ignore[override]
        self, task_id: str, *, after: TaskVersion
    ) -> AsyncGenerator[VersionedEvent, None]:
        """Yields events for `task_id` published after this call, newer than `after`."""
        queue: asyncio.Queue[VersionedEvent] = asyncio.Queue()
        async with self._lock:
            self._subscribers.setdefault(task_id, set()).add(queue)
        try:
            while True:
                versioned = await queue.get()
                if versioned.version.is_after(after):
                    yield versioned
        finally:
            async with self._lock:
                subs = self._subscribers.get(task_id)
                if subs is not None:
                    subs.discard(queue)
                    if not subs:
                        del self._subscribers[task_id]

    async def destroy(self, task_id: str) -> None:
        """Drops all subscribers for `task_id`."""
        async with self._lock:
            self._subscribers.pop(task_id, None)


# --- Identity / context helpers ---------------------------------------------


class SampleUser(User):
    """Minimal authenticated User for tests."""

    def __init__(self, user_name: str = 'test_user') -> None:
        self._user_name = user_name

    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def user_name(self) -> str:
        return self._user_name


def make_context(user: str = 'test_user') -> ServerCallContext:
    """Builds a ServerCallContext for the given user name."""
    return ServerCallContext(user=SampleUser(user))


def streaming_agent_card() -> AgentCard:
    """An AgentCard that advertises streaming support."""
    return AgentCard(capabilities=AgentCapabilities(streaming=True))


def build_send_request(
    text: str,
    task_id: str = '',
    context_id: str = '',
    message_id: str = 'm',
) -> SendMessageRequest:
    """Builds a SendMessageRequest, optionally continuing an existing task."""
    msg = Message(
        role=Role.ROLE_USER,
        message_id=message_id,
        parts=[Part(text=text)],
    )
    if task_id:
        msg.task_id = task_id
    if context_id:
        msg.context_id = context_id
    return SendMessageRequest(message=msg)


async def drain(agen) -> None:  # noqa: ANN001
    """Consumes an async generator to completion, swallowing exceptions."""
    with contextlib.suppress(Exception):
        async for _ in agen:
            pass


async def wait_for_state(
    store: VersionedTaskStore,
    task_id: str,
    state: TaskState,
    context: ServerCallContext,
    *,
    tries: int = 100,
    delay: float = 0.02,
) -> None:
    """Polls the store until the task reaches `state` (for async persistence)."""
    for _ in range(tries):
        stored = await store.get(task_id, context)
        if stored is not None and stored.task.status.state == state:
            return
        await asyncio.sleep(delay)
    raise AssertionError(f'Task {task_id} did not reach {state} in time')


# --- Reusable agent executors -----------------------------------------------


class CompletingAgent(AgentExecutor):
    """Creates the task if needed, goes WORKING, then completes."""

    async def execute(self, context, event_queue) -> None:  # noqa: ANN001
        if context.current_task is None:
            await event_queue.enqueue_event(
                new_task_from_user_message(context.message)
            )
        updater = TaskUpdater(
            event_queue,
            str(context.task_id or ''),
            str(context.context_id or ''),
        )
        await updater.start_work()
        await updater.complete()

    async def cancel(self, context, event_queue) -> None:  # noqa: ANN001
        pass


class ControlledAgent(AgentExecutor):
    """Creates a task, goes WORKING, then completes only when released.

    `working` is set once execution reaches WORKING; the agent then waits on
    `release` before emitting an artifact and completing. Useful for observing a
    task mid-flight from another replica.
    """

    def __init__(self) -> None:
        self.working = asyncio.Event()
        self.release = asyncio.Event()

    async def execute(self, context, event_queue) -> None:  # noqa: ANN001
        if context.current_task is None:
            await event_queue.enqueue_event(
                new_task_from_user_message(context.message)
            )
        updater = TaskUpdater(
            event_queue,
            str(context.task_id or ''),
            str(context.context_id or ''),
        )
        await updater.start_work()
        self.working.set()
        await self.release.wait()
        await updater.add_artifact([Part(text='result')])
        await updater.complete()

    async def cancel(self, context, event_queue) -> None:  # noqa: ANN001
        pass


class LongRunningAgent(AgentExecutor):
    """Goes WORKING then loops, saving periodically until aborted.

    Periodic saves let a remote cancel be observed as a CAS conflict, which
    aborts the execution (`aborted` is set on CancelledError).
    """

    def __init__(self) -> None:
        self.working = asyncio.Event()
        self.aborted = asyncio.Event()

    async def execute(self, context, event_queue) -> None:  # noqa: ANN001
        if context.current_task is None:
            await event_queue.enqueue_event(
                new_task_from_user_message(context.message)
            )
        updater = TaskUpdater(
            event_queue,
            str(context.task_id or ''),
            str(context.context_id or ''),
        )
        await updater.start_work()
        self.working.set()
        try:
            for _ in range(200):
                await asyncio.sleep(0.05)
                await updater.update_status(
                    TaskState.TASK_STATE_WORKING,
                    message=updater.new_agent_message([Part(text='tick')]),
                )
        except asyncio.CancelledError:
            self.aborted.set()
            raise

    async def cancel(self, context, event_queue) -> None:  # noqa: ANN001
        pass


class InputRequiredThenCompleteAgent(AgentExecutor):
    """Asks for input until two user turns exist, then completes.

    Derives the turn purely from durable task history, so it behaves correctly
    regardless of which replica runs each turn.
    """

    async def execute(self, context, event_queue) -> None:  # noqa: ANN001
        task = context.current_task
        if task is None:
            await event_queue.enqueue_event(
                new_task_from_user_message(context.message)
            )
        updater = TaskUpdater(
            event_queue,
            str(context.task_id or ''),
            str(context.context_id or ''),
        )
        user_msgs = 0
        if task is not None:
            user_msgs = sum(1 for m in task.history if m.role == Role.ROLE_USER)

        if task is None or user_msgs <= 1:
            await updater.requires_input(
                message=updater.new_agent_message([Part(text='need input')])
            )
        else:
            await updater.complete()

    async def cancel(self, context, event_queue) -> None:  # noqa: ANN001
        pass


# --- Replica / infra factories ----------------------------------------------


def make_replica(
    store: VersionedTaskStore,
    stream: TaskEventStream,
    agent: AgentExecutor,
) -> DefaultRequestHandlerV2:
    """Builds a handler ('replica') wired to a shared store and stream."""
    return DefaultRequestHandlerV2(
        agent_executor=agent,
        task_store=store,
        agent_card=streaming_agent_card(),
        event_stream=stream,
    )


# --- Fixtures ----------------------------------------------------------------


@pytest.fixture
def shared_store() -> VersionedInMemoryTaskStore:
    """A versioned in-memory store shared across replicas in a test."""
    return VersionedInMemoryTaskStore()


@pytest.fixture
def shared_stream() -> InMemoryTaskEventStream:
    """An in-memory event stream shared across replicas in a test."""
    return InMemoryTaskEventStream()


@pytest.fixture
def context() -> ServerCallContext:
    """A default server call context for 'test_user'."""
    return make_context()
