"""Cross-version aliases for async queue primitives."""

import sys

from typing import Any


if sys.version_info >= (3, 13):
    from asyncio import Queue as AsyncQueue
    from asyncio import QueueShutDown

    def create_async_queue(maxsize: int = 0) -> AsyncQueue[Any]:
        """Create a backwards-compatible async queue object."""
        return AsyncQueue(maxsize=maxsize)
else:
    import culsans

    from culsans import AsyncQueue  # type: ignore[no-redef]
    from culsans import (
        AsyncQueueShutDown as QueueShutDown,  # type: ignore[no-redef]
    )

    def create_async_queue(maxsize: int = 0) -> AsyncQueue[Any]:
        """Create a backwards-compatible async queue object."""
        return culsans.Queue(maxsize=maxsize).async_q  # type: ignore[no-any-return]


__all__ = ['AsyncQueue', 'QueueShutDown', 'create_async_queue']
