import asyncio
import contextlib
import logging
import uuid

from collections.abc import Callable
from datetime import datetime, timezone

from a2a.auth.user import User
from a2a.server.agent_execution.active_task import (
    INTERRUPTED_TASK_STATES,
    TERMINAL_TASK_STATES,
    send_push_notification,
)
from a2a.server.cluster.event_stream import TaskEventStream, VersionedEvent
from a2a.server.cluster.lease import TaskLeaseStore
from a2a.server.cluster.task_store import (
    ConcurrentTaskModificationError,
    VersionedTaskStore,
)
from a2a.server.context import ServerCallContext
from a2a.server.tasks.push_notification_sender import PushNotificationSender
from a2a.types.a2a_pb2 import (
    Message,
    Part,
    Role,
    Task,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)


logger = logging.getLogger(__name__)

ABANDONED_TASK_REASON = 'The replica running this task stopped responding.'


class _OwnerUser(User):
    def __init__(self, owner: str) -> None:
        self._owner = owner

    @property
    def is_authenticated(self) -> bool:
        return bool(self._owner)

    @property
    def user_name(self) -> str:
        return self._owner


def context_for_owner(owner: str) -> ServerCallContext:
    """Builds a call context that `resolve_user_scope` maps to `owner`.

    Deployments using a custom `owner_resolver` must pass their own factory
    to `fail_abandoned_tasks`.
    """
    return ServerCallContext(user=_OwnerUser(owner))


async def fail_task(  # noqa: PLR0913
    task_store: VersionedTaskStore,
    task_id: str,
    context: ServerCallContext,
    *,
    reason: str,
    push_sender: PushNotificationSender | None = None,
    event_stream: TaskEventStream | None = None,
) -> Task | None:
    """Marks the task FAILED unless it is terminal or waiting for input.

    A task in an interrupted state, e.g. INPUT_REQUIRED, already recorded
    the end of its turn, even if the replica died before releasing the
    lease, so it is left as it is.

    The write carries its event, which is also published to `event_stream`,
    so it ends SubscribeToTask streams on every replica. It bumps the
    version, so a replica still running the task stops at its next save.
    Returns the failed task, or None if nothing was written.
    """
    for _ in range(2):
        stored = await task_store.get(task_id, context)
        if stored is None or stored.task.status.state in (
            TERMINAL_TASK_STATES | INTERRUPTED_TASK_STATES
        ):
            return None
        failed = Task()
        failed.CopyFrom(stored.task)
        if failed.status.HasField('message'):
            failed.history.append(failed.status.message)
        status = TaskStatus(
            state=TaskState.TASK_STATE_FAILED,
            message=Message(
                message_id=str(uuid.uuid4()),
                role=Role.ROLE_AGENT,
                task_id=task_id,
                context_id=failed.context_id,
                parts=[Part(text=reason)],
            ),
        )
        status.timestamp.FromDatetime(datetime.now(timezone.utc))
        failed.status.CopyFrom(status)
        event = TaskStatusUpdateEvent(
            task_id=task_id, context_id=failed.context_id, status=status
        )
        try:
            version = await task_store.save(
                failed,
                event=event,
                prev=stored.task,
                prev_version=stored.version,
                context=context,
            )
        except ConcurrentTaskModificationError:
            continue
        if event_stream is not None:
            await event_stream.publish(
                task_id, VersionedEvent(event=event, version=version)
            )
        await send_push_notification(push_sender, task_id, event)
        return failed
    return None


async def fail_abandoned_tasks(  # noqa: PLR0913
    lease_store: TaskLeaseStore,
    task_store: VersionedTaskStore,
    *,
    push_sender: PushNotificationSender | None = None,
    event_stream: TaskEventStream | None = None,
    context_factory: Callable[[str], ServerCallContext] = context_for_owner,
    reason: str = ABANDONED_TASK_REASON,
    claim_ttl: float = 60.0,
    limit: int = 100,
) -> int:
    """Fails the tasks whose turn lease expired. Returns how many it failed.

    Safe to run on every replica at once: each expired lease is claimed by
    one caller. A claim that cannot be completed lapses after `claim_ttl`
    seconds and is retried by a later sweep.
    """
    failed = 0
    for lease in await lease_store.claim_expired(claim_ttl, limit):
        try:
            if await fail_task(
                task_store,
                lease.task_id,
                context_factory(lease.owner),
                reason=reason,
                push_sender=push_sender,
                event_stream=event_stream,
            ):
                failed += 1
        except Exception:
            logger.exception('Recovering task %s failed', lease.task_id)
            continue
        await lease_store.release(lease)
    return failed


class AbandonedTaskSweeper:
    """Runs `fail_abandoned_tasks` every `interval` seconds."""

    def __init__(  # noqa: PLR0913
        self,
        lease_store: TaskLeaseStore,
        task_store: VersionedTaskStore,
        *,
        interval: float = 15.0,
        push_sender: PushNotificationSender | None = None,
        event_stream: TaskEventStream | None = None,
        context_factory: Callable[[str], ServerCallContext] = context_for_owner,
        reason: str = ABANDONED_TASK_REASON,
        claim_ttl: float = 60.0,
        limit: int = 100,
    ) -> None:
        self._lease_store = lease_store
        self._task_store = task_store
        self._interval = interval
        self._push_sender = push_sender
        self._event_stream = event_stream
        self._context_factory = context_factory
        self._reason = reason
        self._claim_ttl = claim_ttl
        self._limit = limit
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        """Starts sweeping in the background. Calling it twice is a no-op."""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(
                self._run(), name='a2a-abandoned-task-sweeper'
            )

    async def aclose(self) -> None:
        """Stops sweeping and waits for the current sweep to end."""
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _run(self) -> None:
        while True:
            try:
                await fail_abandoned_tasks(
                    self._lease_store,
                    self._task_store,
                    push_sender=self._push_sender,
                    event_stream=self._event_stream,
                    context_factory=self._context_factory,
                    reason=self._reason,
                    claim_ttl=self._claim_ttl,
                    limit=self._limit,
                )
            except Exception:
                logger.exception('Abandoned-task sweep failed')
            await asyncio.sleep(self._interval)
