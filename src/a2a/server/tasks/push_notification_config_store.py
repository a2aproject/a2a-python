from abc import ABC, abstractmethod

from a2a.server.context import ServerCallContext
from a2a.types import PushNotificationConfig


class PushNotificationConfigStore(ABC):
    """Interface for storing and retrieving push notification configurations for tasks."""

    @abstractmethod
    async def set_info(
        self,
        task_id: str,
        notification_config: PushNotificationConfig,
        context: ServerCallContext | None = None,
    ) -> None:
        """Sets or updates the push notification configuration for a task."""

    @abstractmethod
    async def get_info(
        self,
        task_id: str,
        context: ServerCallContext | None = None,
    ) -> list[PushNotificationConfig]:
        """Retrieves the push notification configuration for a task."""

    @abstractmethod
    async def delete_info(
        self,
        task_id: str,
        config_id: str | None = None,
        context: ServerCallContext | None = None,
    ) -> None:
        """Deletes the push notification configuration for a task."""
