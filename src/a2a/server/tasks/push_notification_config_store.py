from abc import ABC, abstractmethod

from a2a.types import PushNotificationConfig


class PushNotificationConfigStore(ABC):
    """Interface for storing and retrieving push notification configurations for tasks."""

    @abstractmethod
    async def set_info(
        self, task_id: str, notification_config: PushNotificationConfig
    ) -> None:
        """Sets or updates the push notification configuration for a task."""

    @abstractmethod
    async def get_info(self, task_id: str) -> list[PushNotificationConfig]:
        """Retrieves the push notification configuration for a task."""

    @abstractmethod
    async def delete_info(
        self, task_id: str, config_id: str | None = None
    ) -> None:
        """Deletes the push notification configuration for a task."""
