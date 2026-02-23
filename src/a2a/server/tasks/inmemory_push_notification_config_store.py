import asyncio
import logging

from collections import defaultdict

from a2a.server.context import ServerCallContext
from a2a.server.owner_resolver import OwnerResolver, resolve_user_scope
from a2a.server.tasks.push_notification_config_store import (
    PushNotificationConfigStore,
)
from a2a.types.a2a_pb2 import PushNotificationConfig


logger = logging.getLogger(__name__)


class InMemoryPushNotificationConfigStore(PushNotificationConfigStore):
    """In-memory implementation of PushNotificationConfigStore interface.

    Stores push notification configurations in a nested dictionary in memory,
    keyed by owner then task_id.
    """

    def __init__(
        self,
        owner_resolver: OwnerResolver = resolve_user_scope,
    ) -> None:
        """Initializes the InMemoryPushNotificationConfigStore."""
        self.lock = asyncio.Lock()
        self._push_notification_infos: dict[
            str, dict[str, list[PushNotificationConfig]]
        ] = defaultdict(dict)
        self.owner_resolver = owner_resolver

    def _get_owner_push_notification_infos(self, owner: str) -> dict[str, list[PushNotificationConfig]]:
        return self._push_notification_infos.get(owner, {})

    async def set_info(
        self,
        task_id: str,
        notification_config: PushNotificationConfig,
        context: ServerCallContext,
    ) -> None:
        """Sets or updates the push notification configuration for a task in memory."""
        owner = self.owner_resolver(context)
        async with self.lock:
            owner_infos = self._push_notification_infos[owner]
            if task_id not in owner_infos:
                owner_infos[task_id] = []

            if not notification_config.id:
                notification_config.id = task_id

            # Remove existing config with the same ID
            for config in owner_infos[task_id]:
                if config.id == notification_config.id:
                    owner_infos[task_id].remove(config)
                    break

            owner_infos[task_id].append(notification_config)
            logger.debug(
                'Push notification config for task %s with config id %s for owner %s saved/updated.',
                task_id,
                notification_config.id,
                owner,
            )

    async def get_info(
        self,
        task_id: str,
        context: ServerCallContext,
    ) -> list[PushNotificationConfig]:
        """Retrieves all push notification configurations for a task from memory, for the given owner."""
        owner = self.owner_resolver(context)
        async with self.lock:
            owner_infos = self._get_owner_push_notification_infos(owner)
            return list(owner_infos.get(task_id, []))

    async def delete_info(
        self,
        task_id: str,
        context: ServerCallContext,
        config_id: str | None = None,
    ) -> None:
        """Deletes push notification configurations for a task from memory.

        If config_id is provided, only that specific configuration is deleted.
        If config_id is None, all configurations for the task for the owner are deleted.
        """
        owner = self.owner_resolver(context)
        async with self.lock:
            owner_infos = self._get_owner_push_notification_infos(owner)
            if task_id not in owner_infos:
                logger.warning(
                    'Attempted to delete push notification config for task %s, owner %s that does not exist.',
                    task_id,
                    owner,
                )
                return

            if config_id is None:
                del owner_infos[task_id]
                logger.info(
                    'Deleted all push notification configs for task %s, owner %s.',
                    task_id,
                    owner,
                )
            else:
                configurations = owner_infos[task_id]
                found = False
                for config in configurations:
                    if config.id == config_id:
                        configurations.remove(config)
                        found = True
                        break
                if found:
                    logger.info(
                        'Deleted push notification config %s for task %s, owner %s.',
                        config_id,
                        task_id,
                        owner,
                    )
                    if len(configurations) == 0:
                        del owner_infos[task_id]
                else:
                    logger.warning(
                        'Attempted to delete push notification config %s for task %s, owner %s that does not exist.',
                        config_id,
                        task_id,
                        owner,
                    )

            if not owner_infos:
                del self._push_notification_infos[owner]
