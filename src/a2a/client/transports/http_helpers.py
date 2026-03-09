import json

from collections.abc import AsyncGenerator, Callable, Iterator
from contextlib import contextmanager
from typing import Any, NoReturn

import httpx

from httpx_sse import SSEError, aconnect_sse

from a2a.client.errors import A2AClientError, A2AClientTimeoutError
from a2a.client.middleware import ClientCallContext


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
    **kwargs: Any,
) -> AsyncGenerator[str]:
    """Sends a streaming HTTP request, yielding SSE data strings and handling exceptions."""
    with handle_http_exceptions(status_error_handler):
        async with aconnect_sse(
            httpx_client, method, url, **kwargs
        ) as event_source:
            event_source.response.raise_for_status()
            async for sse in event_source.aiter_sse():
                if not sse.data:
                    continue
                yield sse.data
