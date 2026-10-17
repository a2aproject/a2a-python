"""Internal plumbing for scoping plugin hooks to `DefaultRequestHandlerV2`."""

from __future__ import annotations

import contextlib

from contextvars import ContextVar
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from collections.abc import Iterator

    from a2a.plugins.manager import PluginManager


# Set by `ActiveTask` around push delivery, so `BasePushNotificationSender`
# runs push hooks for the V2 path only. Child tasks created inside the scope
# (e.g. by asyncio.gather) inherit the value.
_push_plugins: ContextVar[PluginManager | None] = ContextVar(
    'a2a_push_plugins', default=None
)


@contextlib.contextmanager
def push_plugins_scope(manager: PluginManager | None) -> Iterator[None]:
    """Makes ``manager`` the push plugin manager inside the block."""
    token = _push_plugins.set(manager)
    try:
        yield
    finally:
        _push_plugins.reset(token)


def current_push_plugins() -> PluginManager | None:
    """Returns the manager set by `push_plugins_scope`, if it has push hooks."""
    manager = _push_plugins.get()
    if manager is None or not manager.has_push_hooks:
        return None
    return manager
