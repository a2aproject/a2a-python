"""Plugin managers and the global registry."""

from __future__ import annotations

import logging

from typing import TYPE_CHECKING, TypeVar

from a2a.plugins.base import (
    ClientPlugin,
    ExecutionPlugin,
    Plugin,
    PushPlugin,
    ServerPlugin,
    TaskObserverPlugin,
)
from a2a.plugins.types import ClientResponse, ServerResponse


if TYPE_CHECKING:
    from collections.abc import Awaitable

    from a2a.plugins.types import (
        ClientRequest,
        PushRequest,
        PushResult,
        ServerRequest,
        TaskEventInfo,
    )
    from a2a.server.agent_execution.context import RequestContext
    from a2a.server.events.event_queue import Event


logger = logging.getLogger(__name__)


async def _run_safely(plugin: Plugin, hook: str, call: Awaitable[None]) -> None:
    """Awaits a hook call, logging and swallowing any exception."""
    try:
        await call
    except Exception:
        logger.exception('A2A plugin %r failed in %s', plugin.name, hook)


class PluginManager(
    ServerPlugin, ClientPlugin, ExecutionPlugin, TaskObserverPlugin, PushPlugin
):
    """Runs registered plugins. SDK call sites depend on this type.

    Since a manager implements every plugin interface, managers can be nested.
    The ``has_*`` properties let call sites skip all plugin work when no
    plugin implements the hooks they would call.
    """

    name = 'plugin-manager'

    @property
    def has_server_hooks(self) -> bool:
        """Whether any plugin implements `ServerPlugin`."""
        return False

    @property
    def has_client_hooks(self) -> bool:
        """Whether any plugin implements `ClientPlugin`."""
        return False

    @property
    def has_execution_hooks(self) -> bool:
        """Whether any plugin implements `ExecutionPlugin`."""
        return False

    @property
    def has_task_hooks(self) -> bool:
        """Whether any plugin implements `TaskObserverPlugin`."""
        return False

    @property
    def has_push_hooks(self) -> bool:
        """Whether any plugin implements `PushPlugin`."""
        return False


_P = TypeVar('_P', bound=Plugin)


def _select(
    plugins: tuple[Plugin, ...], kind: type[_P], has_hooks: str
) -> list[_P]:
    """Returns plugins implementing ``kind``; nested managers only if non-empty."""
    return [
        p
        for p in plugins
        if isinstance(p, kind)
        and (not isinstance(p, PluginManager) or getattr(p, has_hooks))
    ]


class SequentialPluginManager(PluginManager):
    """Runs plugins one after another in registration order.

    - ``before_*`` hooks run in registration order. If one raises, plugins
      that already ran get their ``after_*`` hook with the error, and the
      exception propagates.
    - ``after_*`` hooks run in reverse order. Their exceptions are logged and
      swallowed.
    - Observer hooks (``on_*``) run in registration order. Their exceptions
      are logged and swallowed.
    """

    name = 'sequential'

    def __init__(self, *plugins: Plugin):
        unknown = [p for p in plugins if not isinstance(p, Plugin)]
        if unknown:
            raise TypeError(f'Not A2A plugins: {unknown!r}')
        self._plugins = plugins
        self._server: list[ServerPlugin] = _select(
            plugins, ServerPlugin, 'has_server_hooks'
        )
        self._client: list[ClientPlugin] = _select(
            plugins, ClientPlugin, 'has_client_hooks'
        )
        self._execution: list[ExecutionPlugin] = _select(
            plugins, ExecutionPlugin, 'has_execution_hooks'
        )
        self._task: list[TaskObserverPlugin] = _select(
            plugins, TaskObserverPlugin, 'has_task_hooks'
        )
        self._push: list[PushPlugin] = _select(
            plugins, PushPlugin, 'has_push_hooks'
        )

    @property
    def plugins(self) -> tuple[Plugin, ...]:
        """The registered plugins, in registration order."""
        return self._plugins

    @property
    def has_server_hooks(self) -> bool:  # noqa: D102
        return bool(self._server)

    @property
    def has_client_hooks(self) -> bool:  # noqa: D102
        return bool(self._client)

    @property
    def has_execution_hooks(self) -> bool:  # noqa: D102
        return bool(self._execution)

    @property
    def has_task_hooks(self) -> bool:  # noqa: D102
        return bool(self._task)

    @property
    def has_push_hooks(self) -> bool:  # noqa: D102
        return bool(self._push)

    # --- server ---

    async def before_handler_run(self, request: ServerRequest) -> None:  # noqa: D102
        ran: list[ServerPlugin] = []
        for plugin in self._server:
            try:
                await plugin.before_handler_run(request)
            except Exception as e:
                response = ServerResponse(
                    request.method, None, request.context, error=e
                )
                for done in reversed(ran):
                    await _run_safely(
                        done,
                        'after_handler_run',
                        done.after_handler_run(response),
                    )
                raise
            ran.append(plugin)

    async def after_handler_run(self, response: ServerResponse) -> None:  # noqa: D102
        for plugin in reversed(self._server):
            await _run_safely(
                plugin, 'after_handler_run', plugin.after_handler_run(response)
            )

    # --- client ---

    async def before_client_request(self, request: ClientRequest) -> None:  # noqa: D102
        ran: list[ClientPlugin] = []
        for plugin in self._client:
            try:
                await plugin.before_client_request(request)
            except Exception as e:
                response = ClientResponse(
                    request.method, None, request.context, error=e
                )
                for done in reversed(ran):
                    await _run_safely(
                        done,
                        'after_client_request',
                        done.after_client_request(response),
                    )
                raise
            ran.append(plugin)

    async def after_client_request(self, response: ClientResponse) -> None:  # noqa: D102
        for plugin in reversed(self._client):
            await _run_safely(
                plugin,
                'after_client_request',
                plugin.after_client_request(response),
            )

    # --- execution ---

    async def before_execution(self, request_context: RequestContext) -> None:  # noqa: D102
        ran: list[ExecutionPlugin] = []
        for plugin in self._execution:
            try:
                await plugin.before_execution(request_context)
            except Exception as e:
                for done in reversed(ran):
                    await _run_safely(
                        done,
                        'after_execution',
                        done.after_execution(request_context, e),
                    )
                raise
            ran.append(plugin)

    async def on_execution_event(  # noqa: D102
        self, request_context: RequestContext, event: Event
    ) -> None:
        for plugin in self._execution:
            await _run_safely(
                plugin,
                'on_execution_event',
                plugin.on_execution_event(request_context, event),
            )

    async def after_execution(  # noqa: D102
        self, request_context: RequestContext, error: BaseException | None
    ) -> None:
        for plugin in reversed(self._execution):
            await _run_safely(
                plugin,
                'after_execution',
                plugin.after_execution(request_context, error),
            )

    # --- task ---

    async def on_task_event(self, info: TaskEventInfo) -> None:  # noqa: D102
        for plugin in self._task:
            await _run_safely(
                plugin, 'on_task_event', plugin.on_task_event(info)
            )

    # --- push ---

    async def before_push_send(self, request: PushRequest) -> None:  # noqa: D102
        for plugin in self._push:
            await plugin.before_push_send(request)

    async def on_push_sent(self, result: PushResult) -> None:  # noqa: D102
        for plugin in self._push:
            await _run_safely(
                plugin, 'on_push_sent', plugin.on_push_sent(result)
            )


def sequential(*plugins: Plugin) -> SequentialPluginManager:
    """Returns a manager that runs ``plugins`` in the given order."""
    return SequentialPluginManager(*plugins)


_global_manager: PluginManager = SequentialPluginManager()


def set_global(manager: PluginManager) -> None:
    """Sets the manager used by handlers, clients and push senders created afterwards.

    `DefaultRequestHandlerV2`, clients and `BasePushNotificationSender`
    capture the manager when they are constructed, so call this once at
    startup, before creating them.
    """
    global _global_manager  # noqa: PLW0603
    if not isinstance(manager, PluginManager):
        raise TypeError(
            f'Expected a PluginManager, got {type(manager).__name__}; '
            'wrap plugins with a2a.plugins.sequential(...)'
        )
    _global_manager = manager


def get_global() -> PluginManager:
    """Returns the global manager. Empty unless `set_global` was called."""
    return _global_manager
