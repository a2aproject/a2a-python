import asyncio
import threading

from collections.abc import Iterator
from contextlib import contextmanager, suppress


class TaskChangeNotifier:
    """Wakes in-process waiters when a task is written.

    `TaskManager` signals it after every save, whatever the task store, so a
    long-polling request on the replica that runs the task wakes without
    polling the store. Safe to share across event loops.
    """

    def __init__(self) -> None:
        self._waiters: dict[
            str, set[tuple[asyncio.AbstractEventLoop, asyncio.Event]]
        ] = {}
        self._lock = threading.Lock()

    @contextmanager
    def watch(self, task_id: str) -> Iterator[asyncio.Event]:
        """Yields an event that is set on the next write to `task_id`."""
        waiter = (asyncio.get_running_loop(), asyncio.Event())
        with self._lock:
            self._waiters.setdefault(task_id, set()).add(waiter)
        try:
            yield waiter[1]
        finally:
            with self._lock:
                waiters = self._waiters.get(task_id)
                if waiters is not None:
                    waiters.discard(waiter)
                    if not waiters:
                        del self._waiters[task_id]

    def notify(self, task_id: str) -> None:
        """Wakes everything currently watching `task_id`."""
        with self._lock:
            waiters = list(self._waiters.get(task_id, ()))
        try:
            current = asyncio.get_running_loop()
        except RuntimeError:
            current = None
        for loop, event in waiters:
            if loop is current:
                event.set()
            else:
                # The waiter's loop may close at any moment; then nobody waits.
                with suppress(RuntimeError):
                    loop.call_soon_threadsafe(event.set)
