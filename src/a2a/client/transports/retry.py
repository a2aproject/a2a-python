"""A transport decorator that adds retry logic with exponential backoff."""

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


def default_retry_predicate(error: Exception) -> bool:  # noqa: PLR0911
    """Determines if an error is retryable based on its type and cause.

    Retryable conditions:
    - A2AClientTimeoutError (always)
    - A2AClientError caused by httpx.RequestError (network errors)
    - A2AClientError caused by httpx.HTTPStatusError with status 429, 502, 503, 504
    - A2AClientError caused by grpc.aio.AioRpcError with UNAVAILABLE or RESOURCE_EXHAUSTED

    Non-retryable:
    - Domain-specific errors (TaskNotFoundError, etc.) — inherit A2AError, not A2AClientError
    - A2AClientError caused by json.JSONDecodeError or SSEError
    - A2AClientError with no recognized __cause__
    - Any non-A2AClientError exception
    """
    if isinstance(error, A2AClientTimeoutError):
        return True

    if not isinstance(error, A2AClientError):
        return False

    cause = error.__cause__
    if cause is None:
        return False

    if isinstance(cause, httpx.RequestError):
        return True
    if isinstance(cause, httpx.HTTPStatusError):
        return cause.response.status_code in {429, 502, 503, 504}

    try:
        import grpc  # noqa: PLC0415

        if isinstance(cause, grpc.aio.AioRpcError):
            return cause.code() in {
                grpc.StatusCode.UNAVAILABLE,
                grpc.StatusCode.RESOURCE_EXHAUSTED,
            }
    except ImportError:
        pass

    return False


class RetryTransport(ClientTransport):
    """A transport decorator that adds retry logic with exponential backoff.

    Wraps any ClientTransport and retries failed operations that match
    the retry predicate. Streaming methods (send_message_streaming,
    subscribe) only retry pre-stream failures; once the first event
    is yielded, errors propagate without retry.
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

    def _calculate_delay(self, attempt: int) -> float:
        """Calculates the delay for a given retry attempt using exponential backoff.

        Args:
            attempt: The retry attempt number (1-indexed).

        Returns:
            The delay in seconds before the next retry.
        """
        delay = min(self._base_delay * (2 ** (attempt - 1)), self._max_delay)
        if self._jitter:
            delay = random.uniform(0, delay)  # noqa: S311
        return delay

    async def _execute_with_retry(
        self,
        operation: Callable[[], Awaitable[T]],
        method_name: str,
    ) -> T:
        """Executes an async operation with retry logic.

        Args:
            operation: A zero-argument async callable that performs the transport call.
            method_name: Name of the method being called, used for logging.

        Returns:
            The result of the operation.

        Raises:
            The last exception if all retry attempts are exhausted.
        """
        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                return await operation()
            except Exception as e:  # noqa: PERF203
                last_error = e
                if attempt >= self._max_retries or not self._retry_predicate(e):
                    raise
                delay = self._calculate_delay(attempt + 1)
                logger.warning(
                    'Retry %d/%d for %s after %.2fs: %s',
                    attempt + 1,
                    self._max_retries,
                    method_name,
                    delay,
                    e,
                )
                if self._on_retry is not None:
                    result: Any = self._on_retry(attempt + 1, e, delay)
                    if inspect.isawaitable(result):
                        await result
                await asyncio.sleep(delay)
        raise last_error  # type: ignore[misc]

    async def _execute_streaming_with_retry(
        self,
        operation: Callable[[], AsyncGenerator[StreamResponse]],
        method_name: str,
    ) -> AsyncGenerator[StreamResponse]:
        """Executes a streaming operation with retry logic for pre-stream failures.

        Retries only apply before the first event is yielded. Once streaming
        has started, errors propagate to the caller without retry.

        Args:
            operation: A zero-argument callable returning an async generator.
            method_name: Name of the method being called, used for logging.

        Yields:
            StreamResponse events from the underlying transport.
        """
        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            first = True
            try:
                stream = operation()
                async for event in stream:
                    first = False
                    yield event
            except Exception as e:
                if not first:
                    raise
                last_error = e
                if attempt >= self._max_retries or not self._retry_predicate(e):
                    raise
                delay = self._calculate_delay(attempt + 1)
                logger.warning(
                    'Retry %d/%d for %s after %.2fs: %s',
                    attempt + 1,
                    self._max_retries,
                    method_name,
                    delay,
                    e,
                )
                if self._on_retry is not None:
                    result: Any = self._on_retry(attempt + 1, e, delay)
                    if inspect.isawaitable(result):
                        await result
                await asyncio.sleep(delay)
            else:
                return
        raise last_error  # type: ignore[misc]

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
        """Sends a streaming message request to the agent and yields responses."""
        async for event in self._execute_streaming_with_retry(
            lambda: self._base.send_message_streaming(request, context=context),
            'send_message_streaming',
        ):
            yield event

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
        async for event in self._execute_streaming_with_retry(
            lambda: self._base.subscribe(request, context=context),
            'subscribe',
        ):
            yield event

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
