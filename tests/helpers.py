import asyncio
import contextlib
import logging

from collections.abc import AsyncGenerator, AsyncIterator
from typing import Any, TypeVar


T = TypeVar('T')

logger = logging.getLogger(__name__)


def actively_print_and_yield_async_gen(
    gen: AsyncIterator[T],
    name: str | None = None,
) -> AsyncGenerator[T, Any]:
    """Wraps an async generator to actively print all generated items in a background task.

    - Actively prints all generated items (even if not consumed from the wrapper).
    - Acts as an async generator that produces identical results as the wrapped one.
    - Handles correctly exceptions and stopped iteration.
    - Handles correctly cleanup of the background task (especially when it is force killed).

    Args:
        gen: The source async generator/iterator.
        name: Optional name to include in the log message as '[NAME] Generated: ...'.
              Defaults to str(gen).

    Returns:
        An async generator yielding the same items as `gen`.
    """
    effective_name = name or str(gen)
    _sentinel = object()
    queue: asyncio.Queue[Any] = asyncio.Queue()
    exception: Exception | None = None

    async def _consumer() -> None:
        nonlocal exception
        try:
            async for item in gen:
                logger.info('[%s] Generated: %s', effective_name, item)
                await queue.put(item)
            logger.info('[%s] Ended', effective_name)
        except Exception as e:  # noqa: BLE001
            logger.info('[%s] Raised exception: %s', effective_name, e)
            exception = e
        finally:
            await queue.put(_sentinel)

    task = asyncio.create_task(_consumer())

    async def _producer() -> AsyncGenerator[T, Any]:
        try:
            while True:
                item = await queue.get()
                if item is _sentinel:
                    break
                yield item

            if exception:
                raise exception
        finally:
            # Handle cleanup of the background task.
            if not task.done():
                task.cancel()
                # Wait for task to finish cancellation
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task

    return _producer()
