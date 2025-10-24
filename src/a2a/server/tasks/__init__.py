"""Components for managing tasks within the A2A server."""

import logging

from a2a.server.tasks import (
    BasePushNotificationSender,
    InMemoryPushNotificationConfigStore,
    InMemoryTaskStore,
    PushNotificationConfigStore,
    PushNotificationSender,
    ResultAggregator,
    TaskManager,
    TaskStore,
    TaskUpdater,
)


logger = logging.getLogger(__name__)

try:
    from a2a.server.tasks.database_task_store import (
        DatabaseTaskStore,  # type: ignore
    )
except ImportError as e:
    _original_error = e
    # If the database task store is not available, we can still use in-memory stores.
    logger.debug(
        'DatabaseTaskStore not loaded. This is expected if database dependencies are not installed. Error: %s',
        e,
    )

    class DatabaseTaskStore:  # type: ignore
        """Placeholder for DatabaseTaskStore when dependencies are not installed."""

        def __init__(self, *args, **kwargs):
            raise ImportError(
                'To use DatabaseTaskStore, its dependencies must be installed. '
                'You can install them with \'pip install "a2a-sdk[sql]"\''
            ) from _original_error


try:
    from a2a.server.tasks.database_push_notification_config_store import (
        DatabasePushNotificationConfigStore,  # type: ignore
    )
except ImportError as e:
    _original_error = e
    # If the database push notification config store is not available, we can still use in-memory stores.
    logger.debug(
        'DatabasePushNotificationConfigStore not loaded. This is expected if database dependencies are not installed. Error: %s',
        e,
    )

    class DatabasePushNotificationConfigStore:  # type: ignore
        """Placeholder for DatabasePushNotificationConfigStore when dependencies are not installed."""

        def __init__(self, *args, **kwargs):
            raise ImportError(
                'To use DatabasePushNotificationConfigStore, its dependencies must be installed. '
                'You can install them with \'pip install "a2a-sdk[sql]"\''
            ) from _original_error


__all__ = [
    'BasePushNotificationSender',
    'DatabasePushNotificationConfigStore',
    'DatabaseTaskStore',
    'InMemoryPushNotificationConfigStore',
    'InMemoryTaskStore',
    'PushNotificationConfigStore',
    'PushNotificationSender',
    'ResultAggregator',
    'TaskManager',
    'TaskStore',
    'TaskUpdater',
]
