import asyncio
import logging
import time

from collections.abc import Awaitable, Callable

import httpx

from google.protobuf.json_format import MessageToDict

from a2a.plugins.manager import PluginManager, get_global
from a2a.plugins.types import PushRequest, PushResult
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
        *,
        push_url_validator: Callable[[str], Awaitable[bool]] | None = None,
        plugins: PluginManager | None = None,
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
            push_url_validator: Async callable that returns True to
              accept a push URL, or False to reject it. Defaults to
              None (no library screening). The spec lists these checks
              as SHOULD, so deployments that want the built-in policy
              should pass ``validate_push_notification_url``.
            plugins: Plugin manager whose `PushPlugin` hooks run for every
              delivery, whichever request handler uses this sender.
              Defaults to the global manager at construction time; it is
              independent of the manager passed to the request handler.
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
        self._push_url_validator = push_url_validator
        self._plugins = plugins if plugins is not None else get_global()

    async def send_notification(
        self, task_id: str, event: PushNotificationEvent
    ) -> None:
        """Sends a push notification for an event if configuration exists.

        Best-effort by design: failures to read the configuration store or
        to deliver a notification are logged and do not raise.
        """
        try:
            push_configs = await self._config_store.get_info_for_dispatch(
                task_id
            )
        except Exception:
            # Push delivery is best-effort: an infrastructure failure while
            # reading the config store must not propagate into the task
            # lifecycle.
            logger.exception(
                'Failed to read push notification configs for task_id=%s; '
                'skipping push notification delivery.',
                task_id,
            )
            return
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
        if self._push_url_validator is not None:
            try:
                accepted = await self._push_url_validator(url)
            except Exception:
                logger.exception(
                    'Push URL validator raised for task_id=%s, URL: %s. '
                    'Failing closed and treating the URL as rejected.',
                    task_id,
                    url,
                )
                return False
            if not accepted:
                return False
        plugins = self._plugins if self._plugins.has_push_hooks else None
        request = PushRequest(
            task_id, url, self._build_headers(push_info, task_id), event
        )

        if plugins is not None:
            # before_push_send may add headers or set request.skip. The
            # validated URL is captured above and cannot be redirected.
            try:
                await plugins.before_push_send(request)
            except Exception as e:
                logger.exception(
                    'Push plugin failed before sending for task_id=%s to '
                    'URL: %s.',
                    task_id,
                    url,
                )
                await self._report(plugins, PushResult(request, 0.0, None, e))
                return False
            if request.skip:
                logger.debug(
                    'Push-notification for task_id=%s to URL: %s skipped by a '
                    'plugin',
                    task_id,
                    url,
                )
                await self._report(
                    plugins, PushResult(request, 0.0, None, None, skipped=True)
                )
                return True

        start = time.monotonic()
        status_code: int | None = None
        error: Exception | None = None
        try:
            response = await self._client.post(
                url,
                json=MessageToDict(to_stream_response(event)),
                headers=request.headers,
            )
            status_code = response.status_code
            response.raise_for_status()
        except Exception as e:
            error = e
            logger.exception(
                'Error sending push-notification for task_id=%s to URL: %s.',
                task_id,
                url,
            )
        else:
            logger.info(
                'Push-notification sent for task_id=%s to URL: %s', task_id, url
            )
        if plugins is not None:
            await self._report(
                plugins,
                PushResult(
                    request, time.monotonic() - start, status_code, error
                ),
            )
        return error is None

    def _build_headers(
        self, push_info: TaskPushNotificationConfig, task_id: str
    ) -> dict[str, str]:
        """Builds the delivery headers from the push config."""
        headers: dict[str, str] = {}
        if push_info.token:
            headers['X-A2A-Notification-Token'] = push_info.token
        auth = push_info.authentication
        if push_info.HasField('authentication'):
            if auth.scheme and auth.credentials:
                headers['Authorization'] = f'{auth.scheme} {auth.credentials}'
            elif auth.scheme:
                logger.warning(
                    'Push config %s sets an authentication scheme with no '
                    'credentials; sending no Authorization header for '
                    'task_id=%s',
                    push_info.id,
                    task_id,
                )
        return headers

    @staticmethod
    async def _report(plugins: PluginManager, result: PushResult) -> None:
        """Calls on_push_sent, keeping a raising manager from failing delivery."""
        try:
            await plugins.on_push_sent(result)
        except Exception:
            logger.exception('Push plugin failed in on_push_sent')
