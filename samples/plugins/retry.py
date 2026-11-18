"""Retries read-only unary calls that failed with a transient transport error.

Errors the agent returned (``TaskNotFoundError`` and other A2A errors) are
never retried, and neither are methods that change state or stream: a
``send_message`` that timed out may still have reached the agent.
"""

from __future__ import annotations

import asyncio

from typing import TYPE_CHECKING, Any

import httpx

from a2a import plugins
from a2a.client.errors import A2AClientError, A2AClientTimeoutError


if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Collection

    from a2a.plugins import ClientRequest


DEFAULT_METHODS = frozenset(
    {
        'get_task',
        'list_tasks',
        'get_task_push_notification_config',
        'list_task_push_notification_configs',
        'get_extended_agent_card',
    }
)
"""Methods retried by default: unary calls that only read."""

RETRYABLE_HTTP_STATUSES = frozenset({429, 502, 503, 504})
RETRYABLE_GRPC_CODES = frozenset({'UNAVAILABLE', 'RESOURCE_EXHAUSTED'})


def is_transient(error: A2AClientError) -> bool:
    """Whether a client error is worth retrying.

    True for timeouts, connection failures, and HTTP or gRPC statuses that
    mean "try again later".
    """
    if isinstance(error, A2AClientTimeoutError):
        return True
    cause = error.__cause__
    if isinstance(cause, httpx.HTTPStatusError):
        return cause.response.status_code in RETRYABLE_HTTP_STATUSES
    if isinstance(cause, httpx.TransportError):
        return True
    code = getattr(cause, 'code', None)
    if callable(code):  # grpc.RpcError
        return getattr(code(), 'name', None) in RETRYABLE_GRPC_CODES
    return False


class RetryPlugin(plugins.ClientPlugin):
    """Retries calls with exponential backoff.

    Other plugins see one request: ``before_client_request`` and the final
    ``after_client_request`` run once, however many attempts it takes.
    Interceptors and the transport run once per attempt.

    Args:
        attempts: Total number of attempts, including the first.
        backoff: Delay in seconds before the first retry. Each later retry
            waits twice as long as the previous one.
        methods: SDK method names to retry.
    """

    name = 'retry'

    def __init__(
        self,
        attempts: int = 3,
        backoff: float = 0.1,
        methods: Collection[str] = DEFAULT_METHODS,
    ) -> None:
        if attempts < 1:
            raise ValueError('attempts must be at least 1')
        self.attempts = attempts
        self.backoff = backoff
        self.methods = frozenset(methods)

    async def run_client_call(
        self,
        request: ClientRequest,
        call: Callable[[], Awaitable[Any]],
    ) -> Any:
        """Runs the call, retrying it on transient errors."""
        if request.method not in self.methods:
            return await call()
        attempt = 1
        while True:
            try:
                return await call()
            except A2AClientError as e:
                if attempt >= self.attempts or not is_transient(e):
                    raise
            await asyncio.sleep(self.backoff * 2 ** (attempt - 1))
            attempt += 1
