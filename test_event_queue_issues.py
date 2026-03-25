import asyncio
import time
from a2a.server.events.event_queue import EventQueueSource

async def test_enqueue_blocks_producer():
    source = EventQueueSource()
    sink = await source.tap(max_queue_size=10)
    
    # Simulate a dispatcher that takes 100ms to process an event
    original_put = sink._put_internal
    async def slow_put(event):
        await asyncio.sleep(0.1)
        await original_put(event)
    sink._put_internal = slow_put

    print("Sending 3 events sequentially...")
    start = time.time()
    for i in range(3):
        await source.enqueue_event(f"event_{i}")
    duration = time.time() - start
    print(f"Sequential enqueue took: {duration:.4f}s (Expected ~0s if asynchronous, ~0.3s if synchronous)")

    await source.close(immediate=True)

async def test_slow_consumer_deadlock():
    source = EventQueueSource()
    sink_fast = await source.tap(max_queue_size=1)
    sink_slow = await source.tap(max_queue_size=1)
    
    # 1. Fill sink_slow with event_1
    await source.enqueue_event("event_1")
    await sink_fast.dequeue_event()
    sink_fast.task_done()
    
    # 2. Start enqueueing event_2. The dispatcher puts event_2 into sink_fast,
    # but blocks on putting event_2 into sink_slow because it's full.
    print("Enqueueing event_2...")
    enqueue_task2 = asyncio.create_task(source.enqueue_event("event_2"))
    await asyncio.sleep(0.1) # Let dispatcher process event 2
    
    # 3. Start enqueueing event_3. The dispatcher should be completely blocked
    # and unable to process event_3 for sink_fast!
    print("Enqueueing event_3...")
    enqueue_task3 = asyncio.create_task(source.enqueue_event("event_3"))
    await asyncio.sleep(0.1)
    
    # 4. Try to dequeue event_2 and event_3 from fast sink.
    print("Dequeuing from fast sink...")
    event = await sink_fast.dequeue_event()
    print(f"Fast sink got: {event}")
    sink_fast.task_done()
    
    try:
        # event_3 should NOT be available because dispatcher is stuck on event_2 for slow sink
        event = await asyncio.wait_for(sink_fast.dequeue_event(), timeout=0.2)
        print(f"Fast sink got: {event} (UNEXPECTED)")
    except asyncio.TimeoutError:
        print("Fast sink timed out waiting for event_3. DISPATCHER IS DEADLOCKED!")

    await source.close(immediate=True)


if __name__ == "__main__":
    print("--- Test 1: enqueue_event is unexpectedly synchronous ---")
    asyncio.run(test_enqueue_blocks_producer())
    print("\n--- Test 2: Slow consumer stalls entire event bus ---")
    asyncio.run(test_slow_consumer_deadlock())
