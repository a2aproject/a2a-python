import asyncio
import logging
import time

from collections.abc import Awaitable, Callable

import httpx

from google.protobuf.json_format import MessageToDict

from a2a.plugins._runtime import current_push_plugins
from a2a.plugins.manager import PluginManager
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
        plugins = current_push_plugins()
        try:
            headers: dict[str, str] = {}
            if push_info.token:
                headers['X-A2A-Notification-Token'] = push_info.token
            auth = push_info.authentication
            if push_info.HasField('authentication'):
                if auth.scheme and auth.credentials:
                    headers['Authorization'] = (
                        f'{auth.scheme} {auth.credentials}'
                    )
                elif auth.scheme:
                    logger.warning(
                        'Push config %s sets an authentication scheme with no '
                        'credentials; sending no Authorization header for '
                        'task_id=%s',
                        push_info.id,
                        task_id,
                    )

            if plugins is not None:
                return await self._post_with_plugins(
                    plugins, PushRequest(task_id, url, headers, event)
                )
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

    async def _post_with_plugins(
        self, plugins: PluginManager, request: PushRequest
    ) -> bool:
        """Delivers one notification with `PushPlugin` hooks.

        A raising `before_push_send` fails the delivery. The webhook URL
        cannot be changed by plugins, since it was validated already.
        """
        url = request.url
        try:
            await plugins.before_push_send(request)
        except Exception as e:
            logger.exception(
                'Push plugin failed before sending for task_id=%s to URL: %s.',
                request.task_id,
                request.url,
            )
            await plugins.on_push_sent(PushResult(request, 0.0, None, e))
            return False
        if request.skip:
            logger.debug(
                'Push-notification for task_id=%s to URL: %s skipped by a '
                'plugin',
                request.task_id,
                request.url,
            )
            await plugins.on_push_sent(
                PushResult(request, 0.0, None, None, skipped=True)
            )
            return True

        start = time.monotonic()
        status_code: int | None = None
        error: Exception | None = None
        try:
            response = await self._client.post(
                url,
                json=MessageToDict(to_stream_response(request.event)),
                headers=request.headers,
            )
            status_code = response.status_code
            response.raise_for_status()
        except Exception as e:
            error = e
            logger.exception(
                'Error sending push-notification for task_id=%s to URL: %s.',
                request.task_id,
                request.url,
            )
        await plugins.on_push_sent(
            PushResult(request, time.monotonic() - start, status_code, error)
        )
        if error is None:
            logger.info(
                'Push-notification sent for task_id=%s to URL: %s',
                request.task_id,
                request.url,
            )
        return error is None
