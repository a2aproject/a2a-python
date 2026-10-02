import logging

from abc import ABC, abstractmethod

from a2a.server.context import ServerCallContext
from a2a.types.a2a_pb2 import TaskPushNotificationConfig


logger = logging.getLogger(__name__)


def normalize_push_notification_config(
    task_id: str, notification_config: TaskPushNotificationConfig
) -> TaskPushNotificationConfig:
    """Returns a copy with task_id set and an empty id defaulted to the task id."""
    normalized = TaskPushNotificationConfig()
    normalized.CopyFrom(notification_config)
    normalized.task_id = task_id
    if not normalized.id:
        normalized.id = task_id
    return normalized


class PushNotificationConfigStore(ABC):
    """Interface for storing and retrieving push notification configurations for tasks."""

    @abstractmethod
    async def set_info(
        self,
        task_id: str,
        notification_config: TaskPushNotificationConfig,
        context: ServerCallContext,
    ) -> TaskPushNotificationConfig | None:
        """Sets or updates the push notification configuration for a task.

        Implementations should not mutate notification_config. They store a
        copy normalized by normalize_push_notification_config and return it.
        Returning None is still accepted for existing implementations; the
        request handlers then normalize the request themselves.
        """

    @abstractmethod
    async def get_info(
        self,
        task_id: str,
        context: ServerCallContext,
    ) -> list[TaskPushNotificationConfig]:
        """Retrieves push notification configurations for a task, scoped to the caller.

        This is the user-callable read path. Implementations MUST return
        only configurations owned by the caller (as resolved from
        context).
        """

    async def get_info_for_dispatch(
        self,
        task_id: str,
    ) -> list[TaskPushNotificationConfig]:
        """Retrieves all push notification configurations for a task, across all owners.

        This is the internal read path used by the push-notification
        dispatch loop. Implementations SHOULD override this method to
        return every configuration registered for task_id regardless of
        which user registered it. Authorization already happened at
        registration time and the dispatch path fires every registered
        webhook for the task.

        The default implementation falls back to calling get_info with
        a synthetic empty ServerCallContext. This preserves 1.0
        behavior for subclasses that have not implemented the override
        but is INCORRECT for any deployment with multiple owners: the
        empty context resolves to the empty-string owner partition and
        returns no configs (silently dropping every notification). A
        warning is logged on every call to flag the misconfiguration.
        Custom subclasses MUST override this method to deliver
        notifications correctly in multi-owner deployments.
        """
        logger.warning(
            '%s does not override '
            'PushNotificationConfigStore.get_info_for_dispatch; falling back '
            'to a context-less get_info call which silently drops '
            'notifications in any deployment with multiple owners. Override '
            'get_info_for_dispatch to return all configs for task_id across '
            'every owner.',
            type(self).__name__,
        )
        return await self.get_info(task_id, ServerCallContext())

    @abstractmethod
    async def delete_info(
        self,
        task_id: str,
        context: ServerCallContext,
        config_id: str | None = None,
    ) -> None:
        """Deletes the push notification configuration for a task."""
