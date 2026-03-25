# EventQueue Concurrency Issues Analysis

During an analysis of `src/a2a/server/events/event_queue.py`, several critical concurrency and architectural issues were identified. These bugs break isolation between event consumers, block producers synchronously, and can lead to application deadlocks.

## Issue 1: `enqueue_event` Blocks Producers Synchronously

### Description
The `EventQueueSource.enqueue_event()` method is designed to be an entry point for producers to push events asynchronously. However, immediately after calling `await self._incoming_queue.put(event)`, it explicitly awaits `self._join_incoming_queue()`.

```python
    async def enqueue_event(self, event: Event) -> None:
        try:
            await self._incoming_queue.put(event)
            await self._join_incoming_queue() # <--- blocks until all events are dispatched!
```

Because `_join_incoming_queue()` waits for the underlying `asyncio.Queue` to be completely empty and all `task_done()` calls to be made, the producer is forcefully blocked until the background dispatcher task has fully routed the event. 

### Impact
- **Severe Bottleneck:** Producers are unnecessarily stalled on every enqueue, drastically reducing throughput.
- **Defeats Architecture:** This entirely defeats the purpose of using a decoupled background `_dispatcher_task` and makes `max_queue_size` on the `_incoming_queue` virtually useless (the queue will rarely exceed a size of 1).
- **Cross-Producer Blocking:** If multiple producers concurrently enqueue events, they will all wait for *each other's* events to be dispatched.

---

## Issue 2: Slow Consumers Stalls the Entire Event Bus

### Description
In `EventQueueSource._dispatch_loop()`, events are dispatched to all active child sinks concurrently using `asyncio.gather`:

```python
                if active_sinks:
                    await asyncio.gather(
                        *(
                            sink._put_internal(event)  # noqa: SLF001
                            for sink in active_sinks
                        ),
                        return_exceptions=True,
                    )
```

The `sink._put_internal` method delegates to `await self._queue.put(event)`. If a child sink's queue reaches its `max_queue_size` (because its consumer is slow, blocked, or crashed without closing the sink), this `put()` operation will block.

Because `asyncio.gather` waits for *all* internal puts to complete before proceeding, one full child queue will completely freeze the dispatcher loop.

### Impact
- **Broken Isolation:** A single slow consumer will stop the central dispatcher.
- **Global Event Bus Freeze:** Once the dispatcher is stuck, **no further events** will be routed from the `_incoming_queue` to any other healthy/fast consumers. The entire event architecture grinds to a halt.

---

## Issue 3: Graceful Shutdown Deadlock

### Description
Due to the cascading failure caused by Issue 2, attempting a graceful shutdown can lead to an unrecoverable deadlock.

```python
    async def close(self, immediate: bool = False) -> None:
        # ...
        if immediate:
            # ...
        else:
            # Wait for all already-enqueued events to be dispatched
            await self._join_incoming_queue()
```

If a slow consumer stalls the dispatcher, the `_incoming_queue` will never empty. Calling `source.close(immediate=False)` will result in the parent application hanging indefinitely on `await self._join_incoming_queue()`.

---

## Reproduction Steps

These issues have been verified using an isolated test script:

```python
import asyncio
from a2a.server.events.event_queue import EventQueueSource

async def reproduce():
    source = EventQueueSource()
    sink_fast = await source.tap(max_queue_size=1)
    sink_slow = await source.tap(max_queue_size=1)
    
    # 1. Fill sink_slow with event_1
    await source.enqueue_event("event_1")
    await sink_fast.dequeue_event()
    sink_fast.task_done()
    
    # 2. Dispatcher attempts to put event_2 into sink_slow, but it's full.
    print("Enqueueing event_2...")
    asyncio.create_task(source.enqueue_event("event_2"))
    await asyncio.sleep(0.1) 
    
    # 3. Dispatcher is now completely blocked. Event 3 will never reach sink_fast.
    print("Enqueueing event_3...")
    asyncio.create_task(source.enqueue_event("event_3"))
    await asyncio.sleep(0.1)
    
    print("Dequeuing from fast sink...")
    await sink_fast.dequeue_event() # Dequeues event_2
    sink_fast.task_done()
    
    try:
        await asyncio.wait_for(sink_fast.dequeue_event(), timeout=0.2)
    except asyncio.TimeoutError:
        print("Fast sink timed out waiting for event_3. DISPATCHER IS DEADLOCKED!")

    await source.close(immediate=True)

if __name__ == "__main__":
    asyncio.run(reproduce())
```
