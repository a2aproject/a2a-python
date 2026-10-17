"""Runs `ServerPlugin` hooks around `DefaultRequestHandlerV2` methods."""

from __future__ import annotations

import functools
import inspect

from contextlib import aclosing
from typing import TYPE_CHECKING, Any, Protocol, TypeVar, cast

from a2a.plugins.types import ServerRequest, ServerResponse


if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable

    from a2a.plugins.manager import PluginManager
    from a2a.server.context import ServerCallContext
    from a2a.types.a2a_pb2 import AgentCard


class _PluginHost(Protocol):
    """A request handler that exposes its plugin manager and agent card."""

    @property
    def plugins(self) -> PluginManager: ...

    @property
    def agent_card(self) -> AgentCard: ...


_F = TypeVar('_F', bound='Callable[..., Any]')


def server_plugin_hooks(method: str) -> Callable[[_F], _F]:
    """Runs `before_agent_run` / `after_agent_run` around a handler method.

    Apply as the outermost decorator, so that validation errors raised by
    inner decorators also reach `after_agent_run`. When the handler's manager
    has no server hooks the method runs unchanged.

    Args:
        method: The A2A RPC name passed to the hooks, e.g. ``'GetTask'``.
    """

    def decorator(func: _F) -> _F:
        if inspect.isasyncgenfunction(func):

            @functools.wraps(func)
            async def stream_wrapper(
                self: _PluginHost,
                params: Any,
                context: ServerCallContext,
                *args: Any,
                **kwargs: Any,
            ) -> AsyncGenerator[Any, None]:
                plugins = self.plugins
                if not plugins.has_server_hooks:
                    async with aclosing(
                        func(self, params, context, *args, **kwargs)
                    ) as events:
                        async for event in events:
                            yield event
                    return

                await plugins.before_agent_run(
                    ServerRequest(method, params, context, self.agent_card)
                )
                error: BaseException | None = None
                try:
                    async with aclosing(
                        func(self, params, context, *args, **kwargs)
                    ) as events:
                        async for event in events:
                            await plugins.after_agent_run(
                                ServerResponse(
                                    method,
                                    event,
                                    context,
                                    is_stream_event=True,
                                    is_final=False,
                                )
                            )
                            yield event
                except Exception as e:
                    error = e
                    raise
                finally:
                    # Also runs when the client disconnects (aclose()).
                    await plugins.after_agent_run(
                        ServerResponse(method, None, context, error=error)
                    )

            return cast('_F', stream_wrapper)

        @functools.wraps(func)
        async def unary_wrapper(
            self: _PluginHost,
            params: Any,
            context: ServerCallContext,
            *args: Any,
            **kwargs: Any,
        ) -> Any:
            plugins = self.plugins
            if not plugins.has_server_hooks:
                return await func(self, params, context, *args, **kwargs)

            await plugins.before_agent_run(
                ServerRequest(method, params, context, self.agent_card)
            )
            try:
                result = await func(self, params, context, *args, **kwargs)
            except Exception as e:
                await plugins.after_agent_run(
                    ServerResponse(method, None, context, error=e)
                )
                raise
            await plugins.after_agent_run(
                ServerResponse(method, result, context)
            )
            return result

        return cast('_F', unary_wrapper)

    return decorator
