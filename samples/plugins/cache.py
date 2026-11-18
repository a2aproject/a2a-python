"""Caches ``get_task`` results for tasks in a terminal state.

A task that reached a terminal state (completed, failed, canceled, rejected)
no longer changes, so a repeated ``get_task`` can be answered locally. Tasks
in any other state are always fetched.

A cache hit skips the interceptors and the transport. ``before_client_request``
and ``after_client_request`` still run, so a tracing plugin registered before
this one still records the call.

Entries are keyed by agent, tenant, task id, ``history_length`` and the
``Authorization`` header, so one caller cannot read a task another caller
fetched. Credentials added later by an interceptor (such as
`AuthInterceptor`) are not visible to plugins; use one cache per credential
in that case.
"""

from __future__ import annotations

import hashlib
import time

from collections import OrderedDict
from typing import TYPE_CHECKING, Any

from a2a import plugins
from a2a.types import Task
from a2a.utils.constants import TERMINAL_TASK_STATES


if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Hashable

    from a2a.plugins import ClientRequest


class TerminalTaskCache(plugins.ClientPlugin):
    """An LRU cache of terminal tasks returned by ``get_task``.

    Args:
        max_entries: Number of tasks to keep. The least recently used task
            is dropped first.
        ttl: Seconds an entry stays valid, or ``None`` to keep it until it is
            dropped.
    """

    name = 'terminal-task-cache'

    def __init__(
        self,
        max_entries: int = 1024,
        ttl: float | None = None,
    ) -> None:
        self.max_entries = max_entries
        self.ttl = ttl
        self._entries: OrderedDict[Hashable, tuple[float, Task]] = OrderedDict()

    async def run_client_call(
        self,
        request: ClientRequest,
        call: Callable[[], Awaitable[Any]],
    ) -> Any:
        """Returns a cached copy of the task, or fetches it."""
        if request.method != 'get_task':
            return await call()
        key = _key(request)
        if (cached := self._get(key)) is not None:
            return cached
        task = await call()
        if isinstance(task, Task) and task.status.state in TERMINAL_TASK_STATES:
            self._put(key, task)
        return task

    def _get(self, key: Hashable) -> Task | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        stored_at, task = entry
        if self.ttl is not None and time.monotonic() - stored_at >= self.ttl:
            del self._entries[key]
            return None
        self._entries.move_to_end(key)
        return _copy(task)

    def _put(self, key: Hashable, task: Task) -> None:
        self._entries[key] = (time.monotonic(), _copy(task))
        self._entries.move_to_end(key)
        while len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)


def _key(request: ClientRequest) -> Hashable:
    payload = request.payload
    card = request.agent_card
    return (
        card.name,
        tuple(i.url for i in card.supported_interfaces),
        payload.tenant,
        payload.id,
        payload.history_length if payload.HasField('history_length') else None,
        _credential(request.context.service_parameters or {}),
    )


def _credential(headers: dict[str, str]) -> str | None:
    """A hash of the Authorization header, so the cache holds no secrets."""
    for name, value in headers.items():
        if name.lower() == 'authorization':
            return hashlib.sha256(value.encode()).hexdigest()
    return None


def _copy(task: Task) -> Task:
    copy = Task()
    copy.CopyFrom(task)
    return copy
