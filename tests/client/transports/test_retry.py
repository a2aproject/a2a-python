import asyncio
import json

from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from a2a.client.client import ClientCallContext
from a2a.client.errors import A2AClientError, A2AClientTimeoutError
from a2a.client.transports.base import ClientTransport
from a2a.client.transports.retry import (
    RetryTransport,
    default_retry_predicate,
)
from a2a.types.a2a_pb2 import (
    CancelTaskRequest,
    DeleteTaskPushNotificationConfigRequest,
    GetExtendedAgentCardRequest,
    GetTaskPushNotificationConfigRequest,
    GetTaskRequest,
    ListTaskPushNotificationConfigsRequest,
    ListTasksRequest,
    Message,
    Part,
    SendMessageRequest,
    SendMessageResponse,
    StreamResponse,
    SubscribeToTaskRequest,
    Task,
    TaskPushNotificationConfig,
)
from a2a.utils.errors import InternalError, TaskNotFoundError


@pytest.fixture
def mock_transport() -> AsyncMock:
    return AsyncMock(spec=ClientTransport)


@pytest.fixture
def retry_transport(mock_transport: AsyncMock) -> RetryTransport:
    return RetryTransport(
        mock_transport,
        max_retries=3,
        base_delay=0.01,
        max_delay=0.1,
        jitter=False,
    )


class TestDefaultRetryPredicate:
    def test_timeout_error_is_retryable(self) -> None:
        error = A2AClientTimeoutError('timeout')
        assert default_retry_predicate(error) is True

    def test_network_error_is_retryable(self) -> None:
        cause = httpx.ConnectError('connection refused')
        error = A2AClientError(
            'Network communication error: connection refused'
        )
        error.__cause__ = cause
        assert default_retry_predicate(error) is True

    @pytest.mark.parametrize('status_code', [408, 429, 502, 503, 504])
    def test_retryable_http_status_codes(self, status_code: int) -> None:
        request = httpx.Request('POST', 'http://example.com')
        response = httpx.Response(status_code, request=request)
        cause = httpx.HTTPStatusError(
            'error', request=request, response=response
        )
        error = A2AClientError(f'HTTP Error {status_code}')
        error.__cause__ = cause
        assert default_retry_predicate(error) is True

    @pytest.mark.parametrize('status_code', [400, 401, 403, 404, 500])
    def test_non_retryable_http_status_codes(self, status_code: int) -> None:
        request = httpx.Request('POST', 'http://example.com')
        response = httpx.Response(status_code, request=request)
        cause = httpx.HTTPStatusError(
            'error', request=request, response=response
        )
        error = A2AClientError(f'HTTP Error {status_code}')
        error.__cause__ = cause
        assert default_retry_predicate(error) is False

    def test_json_decode_error_is_not_retryable(self) -> None:
        cause = json.JSONDecodeError('msg', 'doc', 0)
        error = A2AClientError('JSON Decode Error')
        error.__cause__ = cause
        assert default_retry_predicate(error) is False

    def test_domain_error_is_not_retryable(self) -> None:
        error = TaskNotFoundError()
        assert default_retry_predicate(error) is False

    def test_internal_error_is_not_retryable(self) -> None:
        error = InternalError()
        assert default_retry_predicate(error) is False

    def test_client_error_without_cause_is_not_retryable(self) -> None:
        error = A2AClientError('some error')
        assert default_retry_predicate(error) is False

    def test_non_a2a_error_is_not_retryable(self) -> None:
        error = ValueError('not an A2A error')
        assert default_retry_predicate(error) is False

    @pytest.mark.parametrize(
        'status_code, expected',
        [
            ('UNAVAILABLE', True),
            ('RESOURCE_EXHAUSTED', True),
            ('NOT_FOUND', False),
        ],
    )
    def test_grpc_error_retryability(
        self, status_code: str, expected: bool
    ) -> None:
        grpc = pytest.importorskip('grpc')

        class FakeAioRpcError(grpc.aio.AioRpcError):
            def __init__(self, code: object) -> None:
                self._code = code

            def code(self) -> object:
                return self._code

        cause = FakeAioRpcError(getattr(grpc.StatusCode, status_code))
        error = A2AClientError(f'gRPC Error {status_code}')
        error.__cause__ = cause
        assert default_retry_predicate(error) is expected


class TestRetryTransport:
    @pytest.mark.parametrize(
        'method_name, request_obj',
        [
            (
                'send_message',
                SendMessageRequest(message=Message(parts=[Part(text='hello')])),
            ),
            ('get_task', GetTaskRequest(id='t1')),
            ('list_tasks', ListTasksRequest()),
            ('cancel_task', CancelTaskRequest(id='t1')),
            (
                'create_task_push_notification_config',
                TaskPushNotificationConfig(task_id='t1'),
            ),
            (
                'get_task_push_notification_config',
                GetTaskPushNotificationConfigRequest(task_id='t1', id='c1'),
            ),
            (
                'list_task_push_notification_configs',
                ListTaskPushNotificationConfigsRequest(task_id='t1'),
            ),
            (
                'delete_task_push_notification_config',
                DeleteTaskPushNotificationConfigRequest(task_id='t1', id='c1'),
            ),
            ('get_extended_agent_card', GetExtendedAgentCardRequest()),
        ],
    )
    @pytest.mark.asyncio
    async def test_delegates_to_base_transport(
        self,
        mock_transport: AsyncMock,
        retry_transport: RetryTransport,
        method_name: str,
        request_obj: object,
    ) -> None:
        await getattr(retry_transport, method_name)(request_obj)
        getattr(mock_transport, method_name).assert_called_once_with(
            request_obj, context=None
        )

    @pytest.mark.asyncio
    async def test_retries_on_network_error(
        self,
        mock_transport: AsyncMock,
        retry_transport: RetryTransport,
    ) -> None:
        cause = httpx.ConnectError('refused')
        error = A2AClientError('Network communication error: refused')
        error.__cause__ = cause

        expected = Task()
        mock_transport.get_task.side_effect = [error, expected]
        result = await retry_transport.get_task(GetTaskRequest(id='t1'))
        assert result == expected
        assert mock_transport.get_task.call_count == 2

    @pytest.mark.asyncio
    async def test_no_retry_on_domain_error(
        self,
        mock_transport: AsyncMock,
        retry_transport: RetryTransport,
    ) -> None:
        mock_transport.get_task.side_effect = TaskNotFoundError()
        with pytest.raises(TaskNotFoundError):
            await retry_transport.get_task(GetTaskRequest(id='t1'))
        assert mock_transport.get_task.call_count == 1

    @pytest.mark.asyncio
    async def test_no_retry_on_non_retryable_http_status(
        self,
        mock_transport: AsyncMock,
        retry_transport: RetryTransport,
    ) -> None:
        request = httpx.Request('POST', 'http://example.com')
        response = httpx.Response(400, request=request)
        cause = httpx.HTTPStatusError(
            'bad request', request=request, response=response
        )
        error = A2AClientError('HTTP Error 400: bad request')
        error.__cause__ = cause

        mock_transport.send_message.side_effect = error
        with pytest.raises(A2AClientError):
            await retry_transport.send_message(SendMessageRequest())
        assert mock_transport.send_message.call_count == 1

    @pytest.mark.asyncio
    async def test_exponential_backoff_timing(
        self, mock_transport: AsyncMock
    ) -> None:
        transport = RetryTransport(
            mock_transport,
            max_retries=3,
            base_delay=1.0,
            max_delay=30.0,
            jitter=False,
        )
        mock_transport.send_message.side_effect = A2AClientTimeoutError(
            'timeout'
        )

        with patch(
            'a2a.client.transports.retry.asyncio.sleep',
            new_callable=AsyncMock,
        ) as mock_sleep:
            with pytest.raises(A2AClientTimeoutError):
                await transport.send_message(SendMessageRequest())

            assert mock_sleep.call_count == 3
            mock_sleep.assert_any_call(1.0)
            mock_sleep.assert_any_call(2.0)
            mock_sleep.assert_any_call(4.0)

    @pytest.mark.asyncio
    async def test_max_delay_cap(self, mock_transport: AsyncMock) -> None:
        transport = RetryTransport(
            mock_transport,
            max_retries=5,
            base_delay=10.0,
            max_delay=20.0,
            jitter=False,
        )
        mock_transport.send_message.side_effect = A2AClientTimeoutError(
            'timeout'
        )

        with patch(
            'a2a.client.transports.retry.asyncio.sleep',
            new_callable=AsyncMock,
        ) as mock_sleep:
            with pytest.raises(A2AClientTimeoutError):
                await transport.send_message(SendMessageRequest())

            for call_args in mock_sleep.call_args_list:
                assert call_args[0][0] <= 20.0

    @pytest.mark.asyncio
    async def test_jitter_produces_randomized_delays(
        self, mock_transport: AsyncMock
    ) -> None:
        transport = RetryTransport(
            mock_transport,
            max_retries=3,
            base_delay=1.0,
            max_delay=30.0,
            jitter=True,
        )
        mock_transport.send_message.side_effect = A2AClientTimeoutError(
            'timeout'
        )

        with patch(
            'a2a.client.transports.retry.asyncio.sleep',
            new_callable=AsyncMock,
        ) as mock_sleep:
            with pytest.raises(A2AClientTimeoutError):
                await transport.send_message(SendMessageRequest())

            for i, call_args in enumerate(mock_sleep.call_args_list):
                delay = call_args[0][0]
                max_possible = min(1.0 * (2**i), 30.0)
                assert 0 <= delay <= max_possible

    @pytest.mark.asyncio
    async def test_streaming_retries_pre_stream_failure(
        self,
        mock_transport: AsyncMock,
        retry_transport: RetryTransport,
    ) -> None:
        async def success_stream(*args: object, **kwargs: object) -> object:
            yield StreamResponse()
            yield StreamResponse()

        mock_transport.send_message_streaming.side_effect = [
            A2AClientTimeoutError('timeout'),
            success_stream(),
        ]
        events = [
            event
            async for event in retry_transport.send_message_streaming(
                SendMessageRequest()
            )
        ]

        assert len(events) == 2
        assert mock_transport.send_message_streaming.call_count == 2

    @pytest.mark.asyncio
    async def test_streaming_no_retry_mid_stream(
        self,
        mock_transport: AsyncMock,
        retry_transport: RetryTransport,
    ) -> None:
        async def failing_mid_stream(*args: object, **kwargs: object) -> object:
            yield StreamResponse()
            raise A2AClientTimeoutError('mid-stream timeout')

        mock_transport.send_message_streaming.return_value = (
            failing_mid_stream()
        )

        events: list[StreamResponse] = []
        with pytest.raises(A2AClientTimeoutError):
            async for event in retry_transport.send_message_streaming(
                SendMessageRequest()
            ):
                events.append(event)  # noqa: PERF401

        assert len(events) == 1
        assert mock_transport.send_message_streaming.call_count == 1

    @pytest.mark.asyncio
    async def test_subscribe_streaming_retries(
        self,
        mock_transport: AsyncMock,
        retry_transport: RetryTransport,
    ) -> None:
        async def success_stream(*args: object, **kwargs: object) -> object:
            yield StreamResponse()

        mock_transport.subscribe.side_effect = [
            A2AClientTimeoutError('timeout'),
            success_stream(),
        ]
        events = [
            event
            async for event in retry_transport.subscribe(
                SubscribeToTaskRequest(id='t1')
            )
        ]

        assert len(events) == 1
        assert mock_transport.subscribe.call_count == 2

    @pytest.mark.asyncio
    async def test_streaming_max_retries_exhausted(
        self,
        mock_transport: AsyncMock,
        retry_transport: RetryTransport,
    ) -> None:
        mock_transport.send_message_streaming.side_effect = (
            A2AClientTimeoutError('timeout')
        )
        with pytest.raises(A2AClientTimeoutError):
            async for _ in retry_transport.send_message_streaming(
                SendMessageRequest()
            ):
                pass
        assert mock_transport.send_message_streaming.call_count == 4

    @pytest.mark.asyncio
    async def test_custom_retry_predicate(
        self, mock_transport: AsyncMock
    ) -> None:
        transport = RetryTransport(
            mock_transport,
            max_retries=2,
            base_delay=0.01,
            jitter=False,
            retry_predicate=lambda e: isinstance(e, TaskNotFoundError),
        )
        expected = Task()
        mock_transport.get_task.side_effect = [
            TaskNotFoundError(),
            expected,
        ]
        result = await transport.get_task(GetTaskRequest(id='t1'))
        assert result == expected
        assert mock_transport.get_task.call_count == 2

    @pytest.mark.asyncio
    async def test_custom_predicate_rejects_normally_retryable(
        self, mock_transport: AsyncMock
    ) -> None:
        transport = RetryTransport(
            mock_transport,
            max_retries=3,
            base_delay=0.01,
            retry_predicate=lambda e: False,
        )
        mock_transport.send_message.side_effect = A2AClientTimeoutError(
            'timeout'
        )
        with pytest.raises(A2AClientTimeoutError):
            await transport.send_message(SendMessageRequest())
        assert mock_transport.send_message.call_count == 1

    @pytest.mark.asyncio
    async def test_on_retry_async_callback(
        self, mock_transport: AsyncMock
    ) -> None:
        on_retry_mock = AsyncMock()
        transport = RetryTransport(
            mock_transport,
            max_retries=2,
            base_delay=0.01,
            jitter=False,
            on_retry=on_retry_mock,
        )
        error = A2AClientTimeoutError('timeout')
        expected = SendMessageResponse()
        mock_transport.send_message.side_effect = [error, expected]

        await transport.send_message(SendMessageRequest())

        on_retry_mock.assert_called_once_with(1, error, 0.01)

    @pytest.mark.asyncio
    async def test_on_retry_sync_callback(
        self, mock_transport: AsyncMock
    ) -> None:
        calls: list[tuple[int, Exception, float]] = []

        def sync_on_retry(attempt: int, error: Exception, delay: float) -> None:
            calls.append((attempt, error, delay))

        transport = RetryTransport(
            mock_transport,
            max_retries=2,
            base_delay=0.01,
            jitter=False,
            on_retry=sync_on_retry,
        )
        error = A2AClientTimeoutError('timeout')
        expected = SendMessageResponse()
        mock_transport.send_message.side_effect = [error, expected]

        await transport.send_message(SendMessageRequest())

        assert len(calls) == 1
        assert calls[0][0] == 1

    @pytest.mark.asyncio
    async def test_close_delegates_without_retry(
        self,
        mock_transport: AsyncMock,
        retry_transport: RetryTransport,
    ) -> None:
        await retry_transport.close()
        mock_transport.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_context_passed_through(
        self,
        mock_transport: AsyncMock,
        retry_transport: RetryTransport,
    ) -> None:
        context = ClientCallContext(timeout=5.0)
        request = SendMessageRequest(
            message=Message(parts=[Part(text='hello')])
        )
        await retry_transport.send_message(request, context=context)
        mock_transport.send_message.assert_called_once_with(
            request, context=context
        )

    @pytest.mark.asyncio
    async def test_streaming_delegates(
        self,
        mock_transport: AsyncMock,
        retry_transport: RetryTransport,
    ) -> None:
        async def mock_stream(*args: object, **kwargs: object) -> object:
            yield StreamResponse()

        mock_transport.send_message_streaming.return_value = mock_stream()
        request = SendMessageRequest()
        events = [
            event
            async for event in retry_transport.send_message_streaming(request)
        ]

        assert len(events) == 1
        mock_transport.send_message_streaming.assert_called_once_with(
            request, context=None
        )

    @pytest.mark.asyncio
    async def test_end_to_end_retry_within_context_manager(
        self, mock_transport: AsyncMock
    ) -> None:
        expected = SendMessageResponse()
        mock_transport.send_message.side_effect = [
            A2AClientTimeoutError('timeout'),
            expected,
        ]

        async with RetryTransport(
            mock_transport, max_retries=2, base_delay=0.01, jitter=False
        ) as t:
            assert t is not mock_transport
            result = await t.send_message(
                SendMessageRequest(message=Message(parts=[Part(text='hello')]))
            )
            assert result == expected
            assert mock_transport.send_message.call_count == 2
            mock_transport.close.assert_not_awaited()

        mock_transport.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_end_to_end_retry_exhaustion_within_context_manager(
        self, mock_transport: AsyncMock
    ) -> None:
        mock_transport.send_message.side_effect = A2AClientTimeoutError(
            'timeout'
        )

        with pytest.raises(A2AClientTimeoutError):
            async with RetryTransport(
                mock_transport, max_retries=2, base_delay=0.01, jitter=False
            ) as t:
                await t.send_message(SendMessageRequest())

        assert mock_transport.send_message.call_count == 3
        mock_transport.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_max_retries_zero_disables_retry(
        self, mock_transport: AsyncMock
    ) -> None:
        transport = RetryTransport(
            mock_transport,
            max_retries=0,
            base_delay=0.01,
        )
        mock_transport.send_message.side_effect = A2AClientTimeoutError(
            'timeout'
        )
        with pytest.raises(A2AClientTimeoutError):
            await transport.send_message(SendMessageRequest())
        assert mock_transport.send_message.call_count == 1

    def test_invalid_max_retries_raises_value_error(
        self, mock_transport: AsyncMock
    ) -> None:
        with pytest.raises(ValueError, match='max_retries must be >= 0'):
            RetryTransport(mock_transport, max_retries=-1)

    def test_invalid_base_delay_raises_value_error(
        self, mock_transport: AsyncMock
    ) -> None:
        with pytest.raises(ValueError, match='base_delay must be > 0'):
            RetryTransport(mock_transport, base_delay=0)

    def test_invalid_max_delay_raises_value_error(
        self, mock_transport: AsyncMock
    ) -> None:
        with pytest.raises(ValueError, match='max_delay must be > 0'):
            RetryTransport(mock_transport, max_delay=-1)

    @pytest.mark.asyncio
    async def test_on_retry_exception_does_not_break_retry_loop(
        self, mock_transport: AsyncMock
    ) -> None:
        """A buggy on_retry must not replace the original error or stop retries."""

        def bad_callback(attempt: int, error: Exception, delay: float) -> None:
            raise RuntimeError('on_retry blew up')

        transport = RetryTransport(
            mock_transport,
            max_retries=2,
            base_delay=0.01,
            jitter=False,
            on_retry=bad_callback,
        )
        expected = SendMessageResponse()
        mock_transport.send_message.side_effect = [
            A2AClientTimeoutError('timeout'),
            expected,
        ]

        result = await transport.send_message(SendMessageRequest())

        assert result is expected
        assert mock_transport.send_message.call_count == 2

    @pytest.mark.asyncio
    async def test_cancelled_error_during_sleep_propagates(
        self, mock_transport: AsyncMock
    ) -> None:
        """Cancellation during the retry backoff must not be swallowed."""
        transport = RetryTransport(
            mock_transport,
            max_retries=3,
            base_delay=1.0,
            jitter=False,
        )
        mock_transport.send_message.side_effect = A2AClientTimeoutError(
            'timeout'
        )

        async def cancelling_sleep(*_args: object, **_kwargs: object) -> None:
            raise asyncio.CancelledError

        with (
            patch(
                'a2a.client.transports.retry.asyncio.sleep',
                side_effect=cancelling_sleep,
            ),
            pytest.raises(asyncio.CancelledError),
        ):
            await transport.send_message(SendMessageRequest())

        # First attempt ran; cancel hit on the sleep before the second.
        assert mock_transport.send_message.call_count == 1

    @pytest.mark.asyncio
    async def test_streaming_inner_generator_closed_on_consumer_break(
        self, mock_transport: AsyncMock
    ) -> None:
        """A consumer that breaks mid-stream must not leak the inner generator."""
        closed: list[bool] = []

        async def long_stream() -> AsyncGenerator[StreamResponse]:
            try:
                yield StreamResponse()
                yield StreamResponse()
                yield StreamResponse()
            finally:
                closed.append(True)

        mock_transport.send_message_streaming.return_value = long_stream()
        transport = RetryTransport(
            mock_transport,
            max_retries=3,
            base_delay=0.01,
            jitter=False,
        )

        outer = transport.send_message_streaming(SendMessageRequest())
        count = 0
        async for _event in outer:
            count += 1
            if count == 1:
                break
        await outer.aclose()

        assert closed == [True]

    @pytest.mark.asyncio
    async def test_streaming_inner_generator_closed_on_retry(
        self, mock_transport: AsyncMock
    ) -> None:
        """Pre-stream failures must aclose() the inner generator before retry."""
        closed: list[int] = []

        def make_failing_stream(idx: int) -> AsyncGenerator[StreamResponse]:
            async def gen() -> AsyncGenerator[StreamResponse]:
                try:
                    raise A2AClientTimeoutError(f'pre-stream failure {idx}')
                    yield StreamResponse()  # pragma: no cover
                finally:
                    closed.append(idx)

            return gen()

        async def success() -> AsyncGenerator[StreamResponse]:
            yield StreamResponse()

        mock_transport.send_message_streaming.side_effect = [
            make_failing_stream(0),
            make_failing_stream(1),
            success(),
        ]

        transport = RetryTransport(
            mock_transport,
            max_retries=3,
            base_delay=0.001,
            jitter=False,
        )
        events = [
            event
            async for event in transport.send_message_streaming(
                SendMessageRequest()
            )
        ]
        assert len(events) == 1
        assert closed == [0, 1]

    def test_retry_predicate_and_on_retry_callback_aliases_are_exported(
        self,
    ) -> None:
        from a2a.client.transports import (  # noqa: PLC0415
            OnRetryCallback,
            RetryPredicate,
        )

        assert RetryPredicate is not None
        assert OnRetryCallback is not None
