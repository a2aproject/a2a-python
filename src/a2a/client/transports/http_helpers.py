import json

from collections.abc import AsyncGenerator, Callable, Iterator
from contextlib import contextmanager
from typing import Any, NoReturn

import httpx

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


async def parse_sse_stream(
    response: httpx.Response,
) -> AsyncGenerator[tuple[str, str], None]:
    """Yields (event_name, data) from a streaming httpx.Response.

    Conforms to the W3C Server-Sent Events specification.
    """
    event_name = 'message'
    payload_chunks: list[str] = []

    async for line in response.aiter_lines():
        raw_line = line.rstrip('\r\n')

        # Empty line denotes the completion of the current event block
        if not raw_line:
            if payload_chunks:
                yield event_name, '\n'.join(payload_chunks)
            event_name = 'message'
            payload_chunks = []
            continue

        # Ignore comment lines
        if raw_line.startswith(':'):
            continue

        # Split key and value by first colon
        parts = raw_line.split(':', 1)
        key = parts[0]
        val = parts[1] if len(parts) > 1 else ''

        # Strip a single optional leading space
        if val.startswith(' '):
            val = val[1:]

        if key == 'event':
            event_name = val
        elif key == 'data':
            payload_chunks.append(val)

    if payload_chunks:
        yield event_name, '\n'.join(payload_chunks)


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
        ) as response:
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as e:
                # Read upfront streaming error content immediately, otherwise lower-level handlers
                # (e.g. response.json()) crash with 'ResponseNotRead' Access errors.
                await response.aread()
                raise e

            # If the response is not a stream, read it standardly (e.g., upfront JSON-RPC error payload)
            if 'text/event-stream' not in response.headers.get(
                'content-type', ''
            ):
                content = await response.aread()
                yield content.decode('utf-8')
                return

            async for event_name, data in parse_sse_stream(response):
                if not data:
                    continue
                if event_name == 'error':
                    sse_error_handler(data)
                yield data


class _SSEEventSource:
    """Class-based context manager for managing streaming HTTP connections.

    Using a class with `__aenter__` and `__aexit__` instead of an
    `@asynccontextmanager` decorated function prevents event loop finalization
    crashes (specifically `RuntimeError: GeneratorExit thrown into an active
    generator`).

    This error occurs when an outer async generator (e.g., streaming reader)
    is abandoned early, causing the Python event loop to concurrently throw
    GeneratorExit into the nested context manager's suspended generator.
    Coroutine-based context managers are not async generators and avoid this
    collision (see https://bugs.python.org/issue38559).
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

    async def __aenter__(self) -> httpx.Response:
        self._response = await self._client.send(self._request, stream=True)
        return self._response

    async def __aexit__(self, *args: object) -> None:
        if self._response is not None:
            await self._response.aclose()
