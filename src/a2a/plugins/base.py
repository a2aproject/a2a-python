"""Plugin interfaces.

A plugin inherits from the interfaces it needs and overrides only the hooks
it uses; every base method is a no-op.

Interceptor hooks (``before_*``) may modify their argument or reject the call
by raising. Exceptions from ``after_*`` and observer hooks (``on_*``) are
logged and swallowed by the plugin manager.
"""

from __future__ import annotations

from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from a2a.plugins.types import (
        ClientRequest,
        ClientResponse,
        PushRequest,
        PushResult,
        ServerRequest,
        ServerResponse,
        TaskEventInfo,
    )
    from a2a.server.agent_execution.context import RequestContext
    from a2a.server.events.event_queue import Event


class Plugin:
    """Common base of all plugins."""

    name: str = 'unnamed'
    """Name used in logs."""


class ServerPlugin(Plugin):
    """Hooks around each request handled by `DefaultRequestHandlerV2`."""

    async def before_handler_run(self, request: ServerRequest) -> None:
        """Called before the handler processes a request.

        Raise to reject the request; the exception is returned to the caller.
        """

    async def after_handler_run(self, response: ServerResponse) -> None:
        """Called with the handler result, per stream event, and at the end."""


class ClientPlugin(Plugin):
    """Hooks around each request made by an A2A `Client`."""

    async def before_client_request(self, request: ClientRequest) -> None:
        """Called before the request is sent. Raise to cancel it."""

    async def after_client_request(self, response: ClientResponse) -> None:
        """Called with the result, per stream event, and at the end."""


class ExecutionPlugin(Plugin):
    """Hooks around `AgentExecutor.execute`, which runs outside the request."""

    async def before_execution(self, request_context: RequestContext) -> None:
        """Called in the execution task before the agent runs.

        Raise to fail the execution without running the agent.
        """

    async def on_execution_event(
        self, request_context: RequestContext, event: Event
    ) -> None:
        """Called when the agent enqueues an event, before it is processed.

        Observer: runs on the agent's path, so keep it cheap.
        """

    async def after_execution(
        self, request_context: RequestContext, error: BaseException | None
    ) -> None:
        """Called in the execution task after the agent returns or raises."""


class TaskObserverPlugin(Plugin):
    """Observer of task writes."""

    async def on_task_event(self, info: TaskEventInfo) -> None:
        """Called after every successful task write."""


class PushPlugin(Plugin):
    """Hooks around each push notification delivery."""

    async def before_push_send(self, request: PushRequest) -> None:
        """Called before the webhook request.

        May add headers or set ``request.skip``. Raising fails the delivery.
        """

    async def on_push_sent(self, result: PushResult) -> None:
        """Called after the delivery attempt, or when it was skipped."""


class CompletePlugin(ClientPlugin, ServerPlugin):
    """A plugin with both client and server hooks."""
