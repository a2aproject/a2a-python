import inspect
import logging
import warnings

from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from dataclasses import dataclass

from a2a.server.cluster.version import TaskVersion
from a2a.server.events.event_queue import Event


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class VersionedEvent:
    """An event together with the task version produced by applying it."""

    event: Event
    version: TaskVersion


class TaskEventStream(ABC):
    """Delivers task events across replicas."""

    @abstractmethod
    async def publish(self, task_id: str, event: VersionedEvent) -> None:
        """Publishes one event for `task_id` to all replicas."""

    @abstractmethod
    def subscribe(
        self, task_id: str, *, after: TaskVersion
    ) -> AsyncGenerator[VersionedEvent, None]:
        """Yields events for `task_id` newer than `after`."""

    @abstractmethod
    async def destroy(self, task_id: str) -> None:
        """Releases resources for a task that has reached a terminal state."""


_context_support_cache: dict[tuple[type, str], bool] = {}


def _stream_accepts_context(stream: TaskEventStream, method_name: str) -> bool:
    """Temporary solution to support ServerCallContext being passed to TaskEventStream.

    Checks if given method of TaskEventStream accepts context parameter.
    If not, it logs a warning and returns False.

    TODO(ISSUE #1374): remove this function once ServerCallContext is a required parameter on TaskEventStream.
    """
    key = (type(stream), method_name)
    cached = _context_support_cache.get(key)
    if cached is not None:
        return cached

    params = inspect.signature(getattr(stream, method_name)).parameters
    accepts = 'context' in params or any(
        p.kind is p.VAR_KEYWORD for p in params.values()
    )
    _context_support_cache[key] = accepts

    if not accepts:
        message = (
            f'{type(stream).__name__}.{method_name} does not accept a '
            "'context' parameter; ownership checks are skipped for this "
            'TaskEventStream implementation. This will become a required '
            'parameter in a future major version.'
        )
        warnings.warn(message, DeprecationWarning, stacklevel=3)
        logger.warning(message)

    return accepts
