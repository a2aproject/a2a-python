"""uvloop integration for high-performance async operations.

This module provides utilities for enabling uvloop, a fast drop-in replacement
for asyncio's default event loop. uvloop can significantly improve the
performance of async I/O bound applications.

Example usage:

    from a2a.performance import install_uvloop, run_with_uvloop

    # Option 1: Install uvloop as the default event loop
    install_uvloop()

    # Option 2: Run a specific coroutine with uvloop
    async def main():
        # Your async code here
        pass

    run_with_uvloop(main())
"""

import asyncio
import logging
import sys

from collections.abc import Coroutine
from typing import Any, TypeVar


_T = TypeVar('_T')

logger = logging.getLogger(__name__)

_UVLOOP_AVAILABLE = False

try:
    import uvloop

    _UVLOOP_AVAILABLE = True
except ImportError:
    uvloop = None  # type: ignore[assignment]


def is_uvloop_available() -> bool:
    """Check if uvloop is available for use.

    Returns:
        True if uvloop is installed, False otherwise.
    """
    return _UVLOOP_AVAILABLE


def is_uvloop_installed() -> bool:
    """Check if uvloop is currently installed as the event loop policy.

    Returns:
        True if uvloop is the current event loop policy, False otherwise.
    """
    if not _UVLOOP_AVAILABLE:
        return False

    try:
        policy = asyncio.get_event_loop_policy()
        return isinstance(policy, uvloop.EventLoopPolicy)  # type: ignore[union-attr]
    except Exception:
        return False


def install_uvloop() -> bool:
    """Install uvloop as the default event loop policy.

    This should be called before any async code is executed, typically at
    the start of your application.

    Returns:
        True if uvloop was installed successfully, False if not available.

    Raises:
        RuntimeError: If called from a running event loop.

    Example:
        if __name__ == '__main__':
            install_uvloop()
            asyncio.run(main())
    """
    if not _UVLOOP_AVAILABLE:
        logger.debug(
            'uvloop is not available. Install with: pip install a2a-sdk[performance]'
        )
        return False

    try:
        if sys.platform == 'win32':
            logger.warning(
                'uvloop is not supported on Windows. Using default asyncio loop.'
            )
            return False

        asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())  # type: ignore[union-attr]
        logger.info('uvloop event loop policy installed successfully')
        return True
    except Exception as e:
        logger.warning('Failed to install uvloop: %s', e)
        return False


def uninstall_uvloop() -> bool:
    """Uninstall uvloop and restore the default asyncio event loop policy.

    Returns:
        True if uvloop was uninstalled, False if it wasn't installed.
    """
    if not _UVLOOP_AVAILABLE:
        return False

    try:
        asyncio.set_event_loop_policy(asyncio.DefaultEventLoopPolicy())
        logger.info('Restored default asyncio event loop policy')
        return True
    except Exception as e:
        logger.warning('Failed to uninstall uvloop: %s', e)
        return False


def run_with_uvloop(coro: Coroutine[Any, Any, _T]) -> _T:
    """Run a coroutine with uvloop if available, otherwise use asyncio.run().

    This is a convenience function that automatically handles uvloop
    installation and cleanup.

    Args:
        coro: The coroutine to run.

    Returns:
        The result of the coroutine.

    Example:
        async def main():
            client = A2AClient(...)
            await client.send_message(...)

        result = run_with_uvloop(main())
    """
    if sys.platform == 'win32':
        return asyncio.run(coro)

    if not _UVLOOP_AVAILABLE:
        logger.debug('uvloop not available, using asyncio.run()')
        return asyncio.run(coro)

    was_installed = is_uvloop_installed()

    if not was_installed:
        install_uvloop()

    try:
        return asyncio.run(coro)
    finally:
        if not was_installed:
            uninstall_uvloop()


class UvloopRunner:
    """Context manager for running code with uvloop.

    Provides a clean way to enable uvloop for a block of code and
    automatically restore the previous event loop policy on exit.

    Example:
        async def main():
            async with UvloopRunner():
                # uvloop is active here
                client = A2AClient(...)
                await client.send_message(...)
    """

    def __init__(self, *, force: bool = False):
        """Initialize the uvloop runner.

        Args:
            force: If True, raise an error if uvloop is not available.
        """
        self._force = force
        self._was_installed = False
        self._previous_policy: asyncio.AbstractEventLoopPolicy | None = None

    def __enter__(self) -> 'UvloopRunner':
        if not _UVLOOP_AVAILABLE:
            if self._force:
                raise RuntimeError(
                    'uvloop is not available. Install with: '
                    'pip install a2a-sdk[performance]'
                )
            return self

        self._was_installed = is_uvloop_installed()

        if not self._was_installed:
            self._previous_policy = asyncio.get_event_loop_policy()
            install_uvloop()

        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if not _UVLOOP_AVAILABLE:
            return

        if not self._was_installed and self._previous_policy is not None:
            asyncio.set_event_loop_policy(self._previous_policy)


def get_event_loop_optimization_info() -> dict[str, Any]:
    """Get information about the current event loop setup.

    Returns:
        Dictionary with event loop information.
    """
    info = {
        'uvloop_available': _UVLOOP_AVAILABLE,
        'uvloop_installed': is_uvloop_installed(),
        'platform': sys.platform,
        'python_version': sys.version,
    }

    try:
        policy = asyncio.get_event_loop_policy()
        info['policy_class'] = policy.__class__.__name__
    except Exception as e:
        info['policy_error'] = str(e)

    try:
        loop = asyncio.get_running_loop()
        info['loop_class'] = loop.__class__.__name__
    except RuntimeError:
        info['loop_class'] = None

    return info


def optimize_event_loop() -> bool:
    """Optimize the event loop for A2A operations.

    This function installs uvloop if available and appropriate for the
    current platform. It's designed to be called at application startup.

    Returns:
        True if optimization was applied, False otherwise.
    """
    if sys.platform == 'win32':
        logger.debug('Event loop optimization not available on Windows')
        return False

    if is_uvloop_installed():
        logger.debug('uvloop already installed')
        return True

    return install_uvloop()
