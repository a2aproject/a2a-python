"""Runs `ExecutionPlugin` hooks around an `AgentExecutor`."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from a2a.server.agent_execution.agent_executor import AgentExecutor
from a2a.server.events.event_queue import EventQueue


if TYPE_CHECKING:
    from a2a.plugins.manager import PluginManager
    from a2a.server.agent_execution.context import RequestContext
    from a2a.server.events.event_queue import Event


class PluginEventQueue(EventQueue):
    """Calls `on_execution_event` for each event the agent enqueues.

    Wraps the queue passed to `AgentExecutor.execute`. Events the SDK enqueues
    on the underlying queue itself bypass the wrapper and are not reported.
    """

    def __init__(
        self,
        inner: EventQueue,
        plugins: PluginManager,
        request_context: RequestContext,
    ) -> None:
        self._inner = inner
        self._plugins = plugins
        self._request_context = request_context

    async def enqueue_event(self, event: Event) -> None:
        """Reports the event to plugins, then enqueues it."""
        await self._plugins.on_execution_event(self._request_context, event)
        await self._inner.enqueue_event(event)

    def __getattr__(self, name: str) -> Any:
        """Forwards other attributes to the wrapped queue.

        Keeps executors working that rely on the concrete queue type.
        """
        if name == '_inner':
            raise AttributeError(name)
        return getattr(self._inner, name)


class PluginAgentExecutor(AgentExecutor):
    """Runs `before_execution` / `after_execution` around ``execute``."""

    def __init__(self, inner: AgentExecutor, plugins: PluginManager) -> None:
        self._inner = inner
        self._plugins = plugins

    @property
    def inner(self) -> AgentExecutor:
        """The wrapped executor."""
        return self._inner

    async def execute(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        """Runs the wrapped executor with execution hooks."""
        # If a before-hook raises, the manager unwinds the plugins that ran
        # and the exception fails the execution.
        await self._plugins.before_execution(context)
        error: BaseException | None = None
        try:
            await self._inner.execute(
                context, PluginEventQueue(event_queue, self._plugins, context)
            )
        except Exception as e:
            error = e
            raise
        finally:
            await self._plugins.after_execution(context, error)

    async def cancel(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        """Delegates to the wrapped executor without hooks."""
        await self._inner.cancel(context, event_queue)
