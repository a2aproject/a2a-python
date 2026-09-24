import logging

from a2a.server.cluster.event_stream import TaskEventStream, VersionedEvent
from a2a.server.cluster.task_store import (
    ConcurrentTaskModificationError,
    LegacyTaskStoreAdapter,
    StoredTask,
    VersionedTaskStore,
)
from a2a.server.cluster.version import TaskVersion


logger = logging.getLogger(__name__)

try:
    from a2a.server.cluster.database_event_stream import DatabaseTaskEventStream
    from a2a.server.cluster.database_task_store import (
        VersionedDatabaseTaskStore,
    )
except ImportError as e:
    _original_error = e
    logger.debug(
        'Database-backed cluster stores not loaded. This is expected if '
        'database dependencies are not installed. Error: %s',
        _original_error,
    )

    class VersionedDatabaseTaskStore:  # type: ignore[no-redef]
        """Placeholder when database dependencies are not installed."""

        def __init__(self, *args: object, **kwargs: object) -> None:
            raise ImportError(
                'To use VersionedDatabaseTaskStore, its dependencies must be '
                "installed. Install with 'pip install a2a-sdk[sql]'."
            ) from _original_error

    class DatabaseTaskEventStream:  # type: ignore[no-redef]
        """Placeholder when database dependencies are not installed."""

        def __init__(self, *args: object, **kwargs: object) -> None:
            raise ImportError(
                'To use DatabaseTaskEventStream, its dependencies must be '
                "installed. Install with 'pip install a2a-sdk[sql]'."
            ) from _original_error


__all__ = [
    'ConcurrentTaskModificationError',
    'DatabaseTaskEventStream',
    'LegacyTaskStoreAdapter',
    'StoredTask',
    'TaskEventStream',
    'TaskVersion',
    'VersionedDatabaseTaskStore',
    'VersionedEvent',
    'VersionedTaskStore',
]
