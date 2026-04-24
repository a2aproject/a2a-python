from abc import ABC, abstractmethod

from a2a.server.context import ServerCallContext
from a2a.types.a2a_pb2 import TaskPushNotificationConfig


class PushNotificationConfigStore(ABC):
    """Interface for storing and retrieving push notification configurations for tasks."""

    @abstractmethod
    async def set_info(
        self,
        task_id: str,
        notification_config: TaskPushNotificationConfig,
        context: ServerCallContext,
    ) -> None:
        """Sets or updates the push notification configuration for a task."""

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

    @abstractmethod
    async def get_info_for_dispatch(
        self,
        task_id: str,
    ) -> list[TaskPushNotificationConfig]:
        """Retrieves all push notification configurations for a task, across all owners.

        This is the internal read path used by the push-notification
        dispatch loop. Implementations MUST return every configuration
        registered for task_id regardless of which user registered
        it. Authorization already happened at registration time and
        the dispatch path fires every registered webhook for the task.
        """

    @abstractmethod
    async def delete_info(
        self,
        task_id: str,
        context: ServerCallContext,
        config_id: str | None = None,
    ) -> None:
        """Deletes the push notification configuration for a task."""
