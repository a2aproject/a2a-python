import asyncio
import logging

import httpx

from a2a.server.tasks.push_notification_config_store import (
    PushNotificationConfigStore,
)
from a2a.server.tasks.push_notification_sender import PushNotificationSender
from a2a.types import PushNotificationConfig, Task


logger = logging.getLogger(__name__)


class BasePushNotificationSender(PushNotificationSender):
    """Base implementation of PushNotificationSender interface."""

    def __init__(
        self,
        httpx_client: httpx.AsyncClient,
        config_store: PushNotificationConfigStore,
    ) -> None:
        """Initializes the BasePushNotificationSender.

        Args:
            httpx_client: An async HTTP client instance to send notifications.
            config_store: A PushNotificationConfigStore instance to retrieve configurations.
        """
        self._client = httpx_client
        self._config_store = config_store

    async def send_notification(self, task: Task) -> None:
        """Sends a push notification for a task if configuration exists."""
        push_configs = await self._config_store.get_info(task.id)
        if not push_configs:
            return

        awaitables = [
            self._dispatch_notification(task, push_info)
            for push_info in push_configs
        ]
        results = await asyncio.gather(*awaitables)

        if not all(results):
            logger.warning(
                'Some push notifications failed to send for task_id=%s', task.id
            )

    async def _dispatch_notification(
        self, task: Task, push_info: PushNotificationConfig
    ) -> bool:
        url = push_info.url
        try:
            headers = self._build_headers(push_info)
            response = await self._client.post(
                url,
                json=task.model_dump(mode='json', exclude_none=True),
                headers=headers,
            )
            response.raise_for_status()
            logger.info(
                'Push-notification sent for task_id=%s to URL: %s', task.id, url
            )
        except Exception:
            logger.exception(
                'Error sending push-notification for task_id=%s to URL: %s.',
                task.id,
                url,
            )
            return False
        return True

    @staticmethod
    def _authorization_header(
        push_info: PushNotificationConfig,
    ) -> str | None:
        auth = push_info.authentication
        if not auth or not auth.credentials:
            return None
        schemes = [scheme for scheme in auth.schemes if scheme]
        if not schemes:
            return None
        scheme = next(
            (scheme for scheme in schemes if scheme.lower() == 'bearer'),
            schemes[0],
        )
        return f'{scheme} {auth.credentials}'

    def _build_headers(
        self, push_info: PushNotificationConfig
    ) -> dict[str, str] | None:
        headers: dict[str, str] = {}
        if push_info.token:
            headers['X-A2A-Notification-Token'] = push_info.token
        authorization = self._authorization_header(push_info)
        if authorization:
            headers['Authorization'] = authorization
        return headers or None
