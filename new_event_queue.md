# New EventQueue Architecture: EventQueueSource, EventQueueSink, and Dispatcher

## 1. Overview and Architectural Shift

The previous `EventQueue` implementations attempted to maintain a tree of queues and serialize producer traversals using a single lock. This led to complex race conditions: deadlocks when a queue was full during a graceful close, and dropped events when sinks were closed concurrently.

**The New Design** abandons synchronous tree traversal. Instead, it adopts an **asynchronous dispatch model**:
- **`EventQueue`** becomes an abstract base class (interface).
- **`EventQueueSource`** is the parent queue. It acts *only* as a producer-facing buffer. It maintains an `_incoming_queue` and an internal background `_dispatcher_task`.
- **`EventQueueSink`** is the child queue. It acts *only* as a consumer-facing buffer. It maintains a reference to its parent `EventQueueSource`.
- **The Dispatcher Task**: A single background `asyncio.Task` running inside the `EventQueueSource` acts as the *sole writer* to the `EventQueueSink` queues. 

### Why this solves the concurrency issues:
1. **No Producer-Side Traversal Races**: Producers only ever push to the `EventQueueSource`'s `_incoming_queue`. They never traverse the tree.
2. **No Lock Held During I/O**: The `EventQueueSource` uses a single `asyncio.Lock`, but it is *only* used to protect internal state mutations (like adding/removing a sink). It is **never** held while awaiting a `queue.put()`. This entirely eliminates the graceful-close deadlock on full queues.
3. **Strict Ordering**: The background task processes one event at a time from the incoming queue, fanning it out to all sinks using `asyncio.gather`. Order is strictly maintained without complex lock juggling.

---

## 2. Requirements & Guarantees

*   **Decoupled Lifecycles**: EventQueueSinks can be closed independently. Closing a EventQueueSink does not affect the EventQueueSource or other EventQueueSinks.
*   **Cascading Shutdown**: Closing the EventQueueSource propagates the shutdown to all active EventQueueSinks, causing them to raise `QueueShutDown` to their consumers.
*   **Graceful vs. Immediate Close**:
    *   **Immediate (`immediate=True`)**: The EventQueueSource instantly stops accepting events, cancels its dispatcher task, and force-closes all EventQueueSinks. Pending events in buffers are dropped. Blocked consumers raise immediately.
    *   **Graceful (`immediate=False`)**: The EventQueueSource stops accepting new events but allows the `_incoming_queue` to drain. The dispatcher processes all in-flight events and pushes them to the EventQueueSinks. Finally, the EventQueueSinks are gracefully closed, allowing consumers to process their individual buffers before shutting down.
*   **Backpressure**: The `_incoming_queue` and the individual `EventQueueSink` queues are bounded. The dispatcher waits for *all* sinks to accept an event before moving to the next. If a sink is full, backpressure correctly propagates up to the `EventQueueSource`.

---

## 3. Concurrency Assumptions

1. **Single Writer Principle**: Only the `EventQueueSource._dispatcher_task` writes to a `EventQueueSink._queue`. External callers attempting to enqueue directly to a `EventQueueSink` will receive a `RuntimeError`.
2. **Locking and Race Conditions**: The race condition between `enqueue_event` checking `_is_closed` and then awaiting `put()` is solved by **delegating entirely to `queue.shutdown()`**. `enqueue_event` requires NO lock because the underlying `asyncio.Queue` (or `culsans.Queue`) handles shutdown atomically. If `close()` is called while an `enqueue_event` is awaiting `put()`, the queue's internal shutdown logic will instantly wake it up and raise `QueueShutDown`. `_lock` is only used to protect the mutable `_sinks` collection.
3. **EventQueueSink Cleanup**: When a `EventQueueSink` is closed, it notifies the `EventQueueSource` to remove it from the `_sinks` set. This is safe because `EventQueueSource.remove_sink()` only briefly acquires the lock.

---

## 4. Code Implementation

```python
import asyncio
from abc import ABC, abstractmethod
from typing import Any, Set
from culsans import QueueShutDown # Assuming a2a uses culsans or python 3.13 QueueShutDown

class EventQueue(ABC):
    """
    Abstract base class defining the EventQueue interface.
    """

    @abstractmethod
    async def enqueue_event(self, event: Any) -> None:
        pass

    @abstractmethod
    async def dequeue_event(self, no_wait: bool = False) -> Any:
        pass

    @abstractmethod
    def task_done(self) -> None:
        pass

    @abstractmethod
    async def tap(self, max_queue_size: int = 100) -> 'EventQueue':
        pass

    @abstractmethod
    async def close(self, immediate: bool = False) -> None:
        pass

class EventQueueSource(EventQueue):
    def __init__(self, max_queue_size: int = 100):
        # We rely on the underlying queue's thread-safe/async-safe shutdown
        self._incoming_queue = asyncio.Queue(maxsize=max_queue_size)
        self._lock = asyncio.Lock()
        self._sinks: Set['EventQueueSink'] = set()
        self._is_closed = False
        
        self._dispatcher_task = asyncio.create_task(self._dispatch_loop())

        # Internal sink for backward compatibility
        self._default_sink = EventQueueSink(parent=self, max_queue_size=max_queue_size)
        self._sinks.add(self._default_sink)

    async def _dispatch_loop(self) -> None:
        try:
            while True:
                event = await self._incoming_queue.get()
                
                async with self._lock:
                    active_sinks = list(self._sinks)

                if active_sinks:
                    # gather handles backpressure if sinks are full
                    await asyncio.gather(
                        *(sink._put_internal(event) for sink in active_sinks),
                        return_exceptions=True
                    )
                
                self._incoming_queue.task_done()
        except asyncio.CancelledError:
            pass
        except QueueShutDown: # Python 3.13 / culsans queue raises this on get() if shutdown
            pass

    async def tap(self, max_queue_size: int = 100) -> 'EventQueueSink':
        async with self._lock:
            if self._is_closed:
                raise QueueShutDown("Cannot tap a closed EventQueueSource.")
            sink = EventQueueSink(parent=self, max_queue_size=max_queue_size)
            self._sinks.add(sink)
            return sink

    async def remove_sink(self, sink: 'EventQueueSink') -> None:
        async with self._lock:
            self._sinks.discard(sink)

    async def enqueue_event(self, event: Any) -> None:
        # NO LOCK NEEDED. We rely entirely on the underlying queue's atomic shutdown.
        # This prevents the race condition where close() is called after we check _is_closed
        # but before we await put().
        await self._incoming_queue.put(event)

    async def dequeue_event(self, no_wait: bool = False) -> Any:
        return await self._default_sink.dequeue_event(no_wait=no_wait)

    def task_done(self) -> None:
        self._default_sink.task_done()

    async def close(self, immediate: bool = False) -> None:
        async with self._lock:
            if self._is_closed:
                return
            self._is_closed = True
            sinks_to_close = list(self._sinks)

        # 1. Shutdown the incoming queue atomically.
        # This instantly rejects new put() calls and wakes up any blocked put() calls,
        # perfectly resolving the race condition with enqueue_event.
        self._incoming_queue.shutdown(immediate=immediate)

        if immediate:
            self._dispatcher_task.cancel()
            await asyncio.gather(*(sink.close(immediate=True) for sink in sinks_to_close))
        else:
            # Wait for all already-enqueued events to be dispatched
            await self._incoming_queue.join()
            self._dispatcher_task.cancel()
            await asyncio.gather(*(sink.close(immediate=False) for sink in sinks_to_close))


class EventQueueSink(EventQueue):
    def __init__(self, parent: EventQueueSource, max_queue_size: int = 100):
        self._parent = parent
        self._queue = asyncio.Queue(maxsize=max_queue_size)
        self._is_closed = False
        self._lock = asyncio.Lock()

    async def _put_internal(self, event: Any) -> None:
        try:
            await self._queue.put(event)
        except QueueShutDown:
            pass # Sink is closed, safely drop the event

    async def enqueue_event(self, event: Any) -> None:
        raise RuntimeError("EventQueueSinks are read-only. Enqueue events to the parent EventQueueSource.")

    async def dequeue_event(self, no_wait: bool = False) -> Any:
        if no_wait:
            return self._queue.get_nowait()
        return await self._queue.get()

    def task_done(self) -> None:
        self._queue.task_done()

    async def tap(self, max_queue_size: int = 100) -> 'EventQueueSink':
        # Delegate tap to the parent source so all sinks are flat under the source
        return await self._parent.tap(max_queue_size=max_queue_size)

    async def close(self, immediate: bool = False) -> None:
        async with self._lock:
            if self._is_closed:
                return
            self._is_closed = True

        await self._parent.remove_sink(self)

        # Atomic shutdown of the consumer queue
        self._queue.shutdown(immediate=immediate)

        if not immediate:
            await self._queue.join()
```

## 5. Backward Compatibility (Drop-in Replacement Requirements)

To ensure this new architecture can replace the old implementation without breaking existing tests and application logic, several critical details must be maintained:

1. **Instantiation Interface (`EventQueue()`)**: 
   - *Requirement*: Code calls `q = EventQueue()`.
   - *Solution*: Rename `EventQueueSource` back to `EventQueue`. Let the internal child class be named `EventQueueSink` (or similar) that inherits from the same interface.
2. **Exposed `queue` Property**: 
   - *Requirement*: Existing tests interact directly with the underlying `AsyncQueue` (e.g., `assert not q.queue.empty()`, `await q.queue.join()`). 
   - *Solution*: Both the main queue and the sinks must expose a `@property def queue(self)` that returns their underlying `culsans.AsyncQueue`. The `EventQueue` (source) should return its `_default_sink._queue` to maintain the illusion of a single queue.
3. **Context Manager (`__aenter__` / `__aexit__`)**: 
   - *Requirement*: Is it used in the code? **Yes, but ONLY in the test suite** (`tests/server/events/test_event_queue.py`). It is not used in production request handlers.
   - *Solution*: Keep it. Add `__aenter__` and `__aexit__` methods to the interface, calling `await self.close(immediate=False)` on exit, just to keep existing tests passing.
4. **`is_closed()` Method**: 
   - *Requirement*: Is it used in the code? **Yes**. While checking `is_closed()` before enqueueing is generally an error-prone pattern due to race conditions, it is explicitly used in `src/a2a/server/events/event_consumer.py:128` as a workaround: 
     `except (QueueShutDown, asyncio.QueueEmpty): if self.queue.is_closed(): break`. This exists to handle Python 3.10/3.12 async queue edge cases.
   - *Solution*: Keep it. Add `def is_closed(self) -> bool: return self._is_closed` to the interface to support the consumer workaround and the test suite.
5. **Dynamic `tap()` Size**: 
   - *Requirement*: The old `tap()` method did not take a `max_queue_size` argument; it implicitly inherited the parent's size. 
   - *Solution*: `tap()` should default to copying the source's `maxsize`.
6. **Strict `Event` Typing**: 
   - *Requirement*: The old implementation used a strict union type `Event = Message | Task | TaskStatusUpdateEvent | ...`.
   - *Solution*: The new interface must import and use this exact same type alias for strict mypy compliance instead of generic `Any`.
