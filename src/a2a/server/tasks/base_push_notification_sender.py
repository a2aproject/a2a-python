import asyncio
import ipaddress
import logging
import socket
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


def _ip_is_blocked(ip_str: str) -> bool:
    """Whether an address is not a public unicast destination."""
    try:
        addr = ipaddress.ip_address(ip_str.split('%', maxsplit=1)[0])
    except ValueError:
        return True
    return (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    )


def push_url_validation_error(url: str) -> str | None:
    """Return an error string if a push-notification URL is not safe.

    Blocks non-HTTP(S) schemes and hosts that resolve to loopback,
    link-local, private, reserved, multicast, or unspecified addresses
    (e.g. 169.254.169.254 cloud metadata, internal services). A host
    that cannot be resolved is rejected: the POST would fail anyway,
    and failing closed avoids treating resolution errors as a bypass.
    """
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError:
        return 'unparseable URL'
    if parsed.scheme not in ('http', 'https'):
        return f"scheme '{parsed.scheme}' is not http/https"
    host = parsed.hostname
    if not host:
        return 'no hostname'
    port = parsed.port or (443 if parsed.scheme == 'https' else 80)
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return f"host '{host}' could not be resolved"
    for info in infos:
        if _ip_is_blocked(info[4][0]):
            return f"host '{host}' resolves to a non-public address"
    return None


class BasePushNotificationSender(PushNotificationSender):
    """Base implementation of PushNotificationSender interface."""

    def __init__(
        self,
        httpx_client: httpx.AsyncClient,
        config_store: PushNotificationConfigStore,
        context: ServerCallContext | None = None,
        *,
        allow_private_push_urls: bool = False,
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
            allow_private_push_urls: Push-notification URLs are
              client-supplied and the server POSTs to them, which makes
              them an SSRF vector (cloud metadata endpoints, internal
              services). By default each URL is validated at dispatch
              time and non-public targets are dropped. Set this to True
              only in deployments whose legitimate webhooks live on
              private networks (validation is then skipped entirely).
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
        self._allow_private_push_urls = allow_private_push_urls

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
        if not self._allow_private_push_urls:
            validation_error = push_url_validation_error(url)
            if validation_error:
                logger.warning(
                    'Push-notification URL for task_id=%s rejected: %s',
                    task_id,
                    validation_error,
                )
                return False
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
