import asyncio
import inspect
import logging
import random

from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import Any, TypeVar

import httpx

from a2a.client.client import ClientCallContext
from a2a.client.errors import A2AClientError, A2AClientTimeoutError
from a2a.client.transports.base import ClientTransport
from a2a.types.a2a_pb2 import (
    AgentCard,
    CancelTaskRequest,
    DeleteTaskPushNotificationConfigRequest,
    GetExtendedAgentCardRequest,
    GetTaskPushNotificationConfigRequest,
    GetTaskRequest,
    ListTaskPushNotificationConfigsRequest,
    ListTaskPushNotificationConfigsResponse,
    ListTasksRequest,
    ListTasksResponse,
    SendMessageRequest,
    SendMessageResponse,
    StreamResponse,
    SubscribeToTaskRequest,
    Task,
    TaskPushNotificationConfig,
)


logger = logging.getLogger(__name__)

T = TypeVar('T')

RetryPredicate = Callable[[Exception], bool]
OnRetryCallback = Callable[[int, Exception, float], Awaitable[None] | None]

_RETRYABLE_HTTP_STATUS: frozenset[int] = frozenset({408, 429, 502, 503, 504})

# grpc is an optional dependency.
try:
    import grpc as _grpc

    _AioRpcError: Any = _grpc.aio.AioRpcError
    _RETRYABLE_GRPC_CODES: frozenset[Any] = frozenset(
        {
            _grpc.StatusCode.UNAVAILABLE,
            _grpc.StatusCode.RESOURCE_EXHAUSTED,
        }
    )
except ImportError:
    _AioRpcError = None
    _RETRYABLE_GRPC_CODES = frozenset()


def default_retry_predicate(error: Exception) -> bool:  # noqa: PLR0911
    """Returns True for transient errors, False otherwise.

    Retried: A2AClientTimeoutError; A2AClientError caused by httpx network
    errors, HTTP 408/429/502/503/504, or gRPC UNAVAILABLE/RESOURCE_EXHAUSTED.

    Not retried: domain errors (TaskNotFoundError, etc.), HTTP 5xx other than
    502/503/504 (replaying server bugs is not safe), JSON decode / SSE errors.

    The cause is read from ``__cause__`` first (set by ``raise … from e``),
    falling back to ``__context__`` for callers that don't chain explicitly.
    """
    if isinstance(error, A2AClientTimeoutError):
        return True
    if not isinstance(error, A2AClientError):
        return False

    cause = error.__cause__ or error.__context__
    if cause is None:
        return False
    if isinstance(cause, httpx.HTTPStatusError):
        return cause.response.status_code in _RETRYABLE_HTTP_STATUS
    if isinstance(cause, httpx.RequestError):
        return True
    if _AioRpcError is not None and isinstance(cause, _AioRpcError):
        return cause.code() in _RETRYABLE_GRPC_CODES  # pyright: ignore[reportAttributeAccessIssue]
    return False


class RetryTransport(ClientTransport):
    """A transport decorator that retries transient failures with exponential backoff.

    Streaming methods only retry before the first event is yielded.
    """

    def __init__(  # noqa: PLR0913
        self,
        base: ClientTransport,
        *,
        max_retries: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 30.0,
        jitter: bool = True,
        retry_predicate: RetryPredicate | None = None,
        on_retry: OnRetryCallback | None = None,
    ) -> None:
        if max_retries < 0:
            raise ValueError('max_retries must be >= 0')
        if base_delay <= 0:
            raise ValueError('base_delay must be > 0')
        if max_delay <= 0:
            raise ValueError('max_delay must be > 0')
        self._base = base
        self._max_retries = max_retries
        self._base_delay = base_delay
        self._max_delay = max_delay
        self._jitter = jitter
        self._retry_predicate = retry_predicate or default_retry_predicate
        self._on_retry = on_retry

    def _calculate_delay(self, attempt_index: int) -> float:
        delay = min(self._base_delay * (2**attempt_index), self._max_delay)
        if self._jitter:
            delay = random.uniform(0, delay)  # noqa: S311
        return delay

    async def _delay_and_notify(
        self,
        attempt_index: int,
        error: Exception,
        method_name: str,
    ) -> None:
        retry_number = attempt_index + 1
        delay = self._calculate_delay(attempt_index)
        logger.warning(
            'Retry %d/%d for %s after %.2fs: %s',
            retry_number,
            self._max_retries,
            method_name,
            delay,
            error,
        )
        if self._on_retry is not None:
            try:
                result: Any = self._on_retry(retry_number, error, delay)
                if inspect.isawaitable(result):
                    await result
            except asyncio.CancelledError:
                raise
            except Exception:
                # A buggy callback must not break the retry loop.
                logger.exception(
                    'on_retry callback raised for %s; continuing retry',
                    method_name,
                )
        await asyncio.sleep(delay)

    @staticmethod
    async def _safe_aclose(stream: Any) -> None:
        aclose = getattr(stream, 'aclose', None)
        if aclose is None:
            return
        try:
            await aclose()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug(
                'Ignoring error while closing stream during retry cleanup',
                exc_info=True,
            )

    async def _execute_with_retry(
        self,
        operation: Callable[[], Awaitable[T]],
        method_name: str,
    ) -> T:
        attempt = 0
        while True:
            try:
                return await operation()
            except asyncio.CancelledError:  # noqa: PERF203
                raise
            except Exception as e:
                if attempt >= self._max_retries or not self._retry_predicate(e):
                    raise
                await self._delay_and_notify(attempt, e, method_name)
                attempt += 1

    async def _execute_streaming_with_retry(
        self,
        operation: Callable[[], AsyncGenerator[StreamResponse]],
        method_name: str,
    ) -> AsyncGenerator[StreamResponse]:
        # Retry only pre-stream failures. The inner finally closes the inner
        # generator on every exit path (success, retry, exception, consumer
        # break) so transport resources are not leaked.
        attempt = 0
        while True:
            first = True
            stream: Any = None
            try:
                stream = operation()
                try:
                    async for event in stream:
                        first = False
                        yield event
                finally:
                    await self._safe_aclose(stream)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                if (
                    not first
                    or attempt >= self._max_retries
                    or not self._retry_predicate(e)
                ):
                    raise
                await self._delay_and_notify(attempt, e, method_name)
                attempt += 1
            else:
                return

    async def send_message(
        self,
        request: SendMessageRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> SendMessageResponse:
        """Sends a non-streaming message request to the agent."""
        return await self._execute_with_retry(
            lambda: self._base.send_message(request, context=context),
            'send_message',
        )

    async def send_message_streaming(
        self,
        request: SendMessageRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> AsyncGenerator[StreamResponse]:
        """Sends a streaming message request to the agent and yields responses as they arrive."""
        inner = self._execute_streaming_with_retry(
            lambda: self._base.send_message_streaming(request, context=context),
            'send_message_streaming',
        )
        try:
            async for event in inner:
                yield event
        finally:
            await inner.aclose()

    async def get_task(
        self,
        request: GetTaskRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> Task:
        """Retrieves the current state and history of a specific task."""
        return await self._execute_with_retry(
            lambda: self._base.get_task(request, context=context),
            'get_task',
        )

    async def list_tasks(
        self,
        request: ListTasksRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> ListTasksResponse:
        """Retrieves tasks for an agent."""
        return await self._execute_with_retry(
            lambda: self._base.list_tasks(request, context=context),
            'list_tasks',
        )

    async def cancel_task(
        self,
        request: CancelTaskRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> Task:
        """Requests the agent to cancel a specific task."""
        return await self._execute_with_retry(
            lambda: self._base.cancel_task(request, context=context),
            'cancel_task',
        )

    async def create_task_push_notification_config(
        self,
        request: TaskPushNotificationConfig,
        *,
        context: ClientCallContext | None = None,
    ) -> TaskPushNotificationConfig:
        """Sets or updates the push notification configuration for a specific task."""
        return await self._execute_with_retry(
            lambda: self._base.create_task_push_notification_config(
                request, context=context
            ),
            'create_task_push_notification_config',
        )

    async def get_task_push_notification_config(
        self,
        request: GetTaskPushNotificationConfigRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> TaskPushNotificationConfig:
        """Retrieves the push notification configuration for a specific task."""
        return await self._execute_with_retry(
            lambda: self._base.get_task_push_notification_config(
                request, context=context
            ),
            'get_task_push_notification_config',
        )

    async def list_task_push_notification_configs(
        self,
        request: ListTaskPushNotificationConfigsRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> ListTaskPushNotificationConfigsResponse:
        """Lists push notification configurations for a specific task."""
        return await self._execute_with_retry(
            lambda: self._base.list_task_push_notification_configs(
                request, context=context
            ),
            'list_task_push_notification_configs',
        )

    async def delete_task_push_notification_config(
        self,
        request: DeleteTaskPushNotificationConfigRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> None:
        """Deletes the push notification configuration for a specific task."""
        await self._execute_with_retry(
            lambda: self._base.delete_task_push_notification_config(
                request, context=context
            ),
            'delete_task_push_notification_config',
        )

    async def subscribe(
        self,
        request: SubscribeToTaskRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> AsyncGenerator[StreamResponse]:
        """Reconnects to get task updates."""
        inner = self._execute_streaming_with_retry(
            lambda: self._base.subscribe(request, context=context),
            'subscribe',
        )
        try:
            async for event in inner:
                yield event
        finally:
            await inner.aclose()

    async def get_extended_agent_card(
        self,
        request: GetExtendedAgentCardRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> AgentCard:
        """Retrieves the Extended AgentCard."""
        return await self._execute_with_retry(
            lambda: self._base.get_extended_agent_card(
                request, context=context
            ),
            'get_extended_agent_card',
        )

    async def close(self) -> None:
        """Closes the transport."""
        await self._base.close()
