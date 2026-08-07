import asyncio
import ipaddress
import logging
import urllib.parse

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
from a2a.types.a2a_pb2 import TaskPushNotificationConfig
from a2a.utils.proto_utils import to_stream_response


logger = logging.getLogger(__name__)


class BasePushNotificationSender(PushNotificationSender):
    """Base implementation of PushNotificationSender interface."""

    def __init__(
        self,
        httpx_client: httpx.AsyncClient,
        config_store: PushNotificationConfigStore,
        context: ServerCallContext | None = None,
    ) -> None:
        """Initializes the BasePushNotificationSender.

        Args:
            httpx_client: An async HTTP client instance to send notifications.
            config_store: A PushNotificationConfigStore instance to
              retrieve configurations.
            context: Deprecated and ignored. Accepted only for
              backward compatibility with 1.0 callers that constructed
              the sender with a (typically dummy) ServerCallContext.
              Pass None (the default) in new code. A non-None
              value logs a deprecation warning and is otherwise
              ignored.
        """
        if context is not None:
            logger.warning(
                'BasePushNotificationSender no longer uses the context '
                'parameter; it is accepted only for backward compatibility '
                'with 1.0 and will be removed in a future major version. '
                'Push notifications now fan out across all owners via '
                'PushNotificationConfigStore.get_info_for_dispatch; the '
                'caller identity is not carried into dispatch. Drop the '
                'context argument from the constructor call.'
            )
        self._client = httpx_client
        self._config_store = config_store

    async def send_notification(
        self, task_id: str, event: PushNotificationEvent
    ) -> None:
        """Sends a push notification for an event if configuration exists."""
        push_configs = await self._config_store.get_info_for_dispatch(task_id)
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
        push_info: TaskPushNotificationConfig,
        task_id: str,
    ) -> bool:
        url = push_info.url
        try:
            self._validate_push_url(url)

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

    @staticmethod
    def _validate_push_url(url: str) -> None:
        """Validates a push notification URL to prevent SSRF.

        Only http/https URLs with a hostname that is not a private,
        link-local, unspecified, multicast or reserved address are
        allowed. Loopback addresses are permitted so that local
        notification receivers keep working. Cloud metadata endpoints
        are blocked explicitly. Hostnames that are not literal IPs are
        resolved by the HTTP client at connection time.

        Args:
            url: The push notification URL to validate.

        Raises:
            ValueError: If the URL is not a valid http(s) URL, or if it
                points at a blocked address.
        """
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ('http', 'https'):
            raise ValueError(
                f'Push notification URL must use http or https, got: '
                f'{parsed.scheme}'
            )
        hostname = parsed.hostname
        if not hostname:
            raise ValueError('Invalid push notification URL: no hostname')
        if hostname in (
            '169.254.169.254',
            'metadata.google.internal',
            'metadata.azure.com',
        ):
            raise ValueError(
                f'Push notification URL must not point to cloud metadata: '
                f'{hostname}'
            )
        try:
            ip = ipaddress.ip_address(hostname)
        except ValueError:
            return  # Hostname, not a literal IP; resolved at connection time
        if ip.is_loopback:
            return  # Loopback receivers (e.g. local endpoints) are allowed
        if (
            ip.is_private
            or ip.is_link_local
            or ip.is_unspecified
            or ip.is_multicast
            or ip.is_reserved
        ):
            raise ValueError(
                f'Push notification URL must not point to private/internal '
                f'network: {hostname}'
            )
