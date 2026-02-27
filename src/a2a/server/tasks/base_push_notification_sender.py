import asyncio
import logging

import httpx

from google.protobuf.json_format import MessageToDict

from a2a.server.context import ServerCallContext
from a2a.server.tasks.push_notification_config_store import (
    PushNotificationConfigStore,
)
from a2a.server.tasks.push_notification_sender import (
    PushNotificationEvent,
    PushNotificationSender,
)
from a2a.types.a2a_pb2 import PushNotificationConfig
from a2a.utils.proto_utils import to_stream_response


logger = logging.getLogger(__name__)


class BasePushNotificationSender(PushNotificationSender):
    """Base implementation of PushNotificationSender interface."""

    def __init__(
        self,
        httpx_client: httpx.AsyncClient,
        config_store: PushNotificationConfigStore,
        context: ServerCallContext,
    ) -> None:
        """Initializes the BasePushNotificationSender.

        Args:
            httpx_client: An async HTTP client instance to send notifications.
            config_store: A PushNotificationConfigStore instance to retrieve configurations.
            context: The `ServerCallContext` that this push notification is produced under.
        """
        self._client = httpx_client
        self._config_store = config_store
        self._call_context: ServerCallContext = context

    async def send_notification(
        self, task_id: str, event: PushNotificationEvent
    ) -> None:
        """Sends a push notification for an event if configuration exists."""
        push_configs = await self._config_store.get_info(
            task_id, self._call_context
        )
        if not push_configs:
            return

        awaitables = [
            self._dispatch_notification(event, push_info, task_id)
            for push_info in push_configs
        ]
        results = await asyncio.gather(*awaitables)

        if not all(results):
            logger.warning(
                'Some push notifications failed to send for task_id=%s', task_id
            )

    async def _dispatch_notification(
        self,
        event: PushNotificationEvent,
        push_info: PushNotificationConfig,
        task_id: str,
    ) -> bool:
        url = push_info.url
        try:
            headers = None
            if push_info.token:
                headers = {'X-A2A-Notification-Token': push_info.token}

            response = await self._client.post(
                url,
                json=MessageToDict(to_stream_response(event)),
                headers=headers,
            )
            response.raise_for_status()
            logger.info(
                'Push-notification sent for task_id=%s to URL: %s', task_id, url
            )
        except Exception:
            logger.exception(
                'Error sending push-notification for task_id=%s to URL: %s.',
                task_id,
                url,
            )
            return False
        return True
