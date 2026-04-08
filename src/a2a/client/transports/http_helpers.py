import json

from collections.abc import AsyncGenerator, Callable, Iterator
from contextlib import contextmanager
from typing import Any, NoReturn

import httpx

from httpx_sse import EventSource, SSEError

from a2a.client.client import ClientCallContext
from a2a.client.errors import A2AClientError, A2AClientTimeoutError


def _default_sse_error_handler(sse_data: str) -> NoReturn:
    raise A2AClientError(f'SSE stream error event received: {sse_data}')


@contextmanager
def handle_http_exceptions(
    status_error_handler: Callable[[httpx.HTTPStatusError], NoReturn]
    | None = None,
) -> Iterator[None]:
    """Handles common HTTP exceptions for REST and JSON-RPC transports.

    Args:
        status_error_handler: Optional handler for `httpx.HTTPStatusError`.
            If provided, this handler should raise an appropriate domain-specific exception.
            If not provided, a default `A2AClientError` will be raised.
    """
    try:
        yield
    except httpx.TimeoutException as e:
        raise A2AClientTimeoutError('Client Request timed out') from e
    except httpx.HTTPStatusError as e:
        if status_error_handler:
            status_error_handler(e)
        raise A2AClientError(f'HTTP Error {e.response.status_code}: {e}') from e
    except SSEError as e:
        raise A2AClientError(
            f'Invalid SSE response or protocol error: {e}'
        ) from e
    except httpx.RequestError as e:
        raise A2AClientError(f'Network communication error: {e}') from e
    except json.JSONDecodeError as e:
        raise A2AClientError(f'JSON Decode Error: {e}') from e


def get_http_args(context: ClientCallContext | None) -> dict[str, Any]:
    """Extracts HTTP arguments from the client call context."""
    http_kwargs: dict[str, Any] = {}
    if context and context.service_parameters:
        http_kwargs['headers'] = context.service_parameters.copy()
    if context and context.timeout is not None:
        http_kwargs['timeout'] = httpx.Timeout(context.timeout)
    return http_kwargs


async def send_http_request(
    httpx_client: httpx.AsyncClient,
    request: httpx.Request,
    status_error_handler: Callable[[httpx.HTTPStatusError], NoReturn]
    | None = None,
) -> dict[str, Any]:
    """Sends an HTTP request and parses the JSON response, handling common exceptions."""
    with handle_http_exceptions(status_error_handler):
        response = await httpx_client.send(request)
        response.raise_for_status()
        return response.json()


async def send_http_stream_request(
    httpx_client: httpx.AsyncClient,
    method: str,
    url: str,
    status_error_handler: Callable[[httpx.HTTPStatusError], NoReturn]
    | None = None,
    sse_error_handler: Callable[[str], NoReturn] = _default_sse_error_handler,
    **kwargs: Any,
) -> AsyncGenerator[str]:
    """Sends a streaming HTTP request, yielding SSE data strings and handling exceptions.

    Args:
        httpx_client: The async HTTP client.
        method: The HTTP method (e.g. 'POST', 'GET').
        url: The URL to send the request to.
        status_error_handler: Handler for HTTP status errors. Should raise an
            appropriate domain-specific exception.
        sse_error_handler: Handler for SSE error events. Called with the
            raw SSE data string when an ``event: error`` SSE event is received.
            Should raise an appropriate domain-specific exception.
        **kwargs: Additional keyword arguments forwarded to ``aconnect_sse``.
    """
    with handle_http_exceptions(status_error_handler):
        async with _SSEEventSource(
            httpx_client, method, url, **kwargs
        ) as event_source:
            try:
                event_source.response.raise_for_status()
            except httpx.HTTPStatusError as e:
                # Read upfront streaming error content immediately, otherwise lower-level handlers
                # (e.g. response.json()) crash with 'ResponseNotRead' Access errors.
                await event_source.response.aread()
                raise e

            # If the response is not a stream, read it standardly (e.g., upfront JSON-RPC error payload)
            if 'text/event-stream' not in event_source.response.headers.get(
                'content-type', ''
            ):
                content = await event_source.response.aread()
                yield content.decode('utf-8')
                return

            async for sse in event_source.aiter_sse():
                if not sse.data:
                    continue
                if sse.event == 'error':
                    sse_error_handler(sse.data)
                yield sse.data


class _SSEEventSource:
    """Class-based replacement for ``httpx_sse.aconnect_sse``.

    ``aconnect_sse`` is an ``@asynccontextmanager`` whose internal async
    generator gets tracked by the event loop. When the enclosing async
    generator is abandoned, the event loop's generator cleanup collides
    with the cascading cleanup — see https://bugs.python.org/issue38559.

    Plain ``__aenter__``/``__aexit__`` coroutines avoid this entirely.
    """

    def __init__(
        self,
        client: httpx.AsyncClient,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> None:
        headers = httpx.Headers(kwargs.pop('headers', None))
        headers.setdefault('Accept', 'text/event-stream')
        headers.setdefault('Cache-Control', 'no-store')
        self._request = client.build_request(
            method, url, headers=headers, **kwargs
        )
        self._client = client
        self._response: httpx.Response | None = None

    async def __aenter__(self) -> EventSource:
        self._response = await self._client.send(self._request, stream=True)
        return EventSource(self._response)

    async def __aexit__(self, *args: object) -> None:
        if self._response is not None:
            await self._response.aclose()
