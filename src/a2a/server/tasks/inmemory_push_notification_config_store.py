import asyncio
import logging

from a2a.server.tasks.push_notification_config_store import (
    PushNotificationConfigStore,
)
from a2a.types.a2a_pb2 import PushNotificationConfig


logger = logging.getLogger(__name__)


class InMemoryPushNotificationConfigStore(PushNotificationConfigStore):
    """In-memory implementation of PushNotificationConfigStore interface.

    Stores push notification configurations in memory
    """

    def __init__(self) -> None:
        """Initializes the InMemoryPushNotificationConfigStore."""
        self.lock = asyncio.Lock()
        self._push_notification_infos: dict[
            str, list[PushNotificationConfig]
        ] = {}

    async def set_info(
        self, task_id: str, notification_config: PushNotificationConfig
    ) -> None:
        """Sets or updates the push notification configuration for a task in memory."""
        async with self.lock:
            if task_id not in self._push_notification_infos:
                self._push_notification_infos[task_id] = []

            if not notification_config.id:
                notification_config.id = task_id

            for config in self._push_notification_infos[task_id]:
                if config.id == notification_config.id:
                    self._push_notification_infos[task_id].remove(config)
                    break

            self._push_notification_infos[task_id].append(notification_config)

    async def get_info(self, task_id: str) -> list[PushNotificationConfig]:
        """Retrieves the push notification configuration for a task from memory."""
        async with self.lock:
            return self._push_notification_infos.get(task_id) or []

    async def delete_info(
        self, task_id: str, config_id: str | None = None
    ) -> None:
        """Deletes the push notification configuration for a task from memory."""
        async with self.lock:
            if config_id is None:
                config_id = task_id

            if task_id in self._push_notification_infos:
                configurations = self._push_notification_infos[task_id]
                if not configurations:
                    return

                for config in configurations:
                    if config.id == config_id:
                        configurations.remove(config)
                        break

                if len(configurations) == 0:
                    del self._push_notification_infos[task_id]
