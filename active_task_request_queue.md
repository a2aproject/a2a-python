# Plan: ActiveTask Request Queue Integration

## 1. Overview and Goal
The objective is to refactor `ActiveTask` to support a continuous stream of `RequestContext` objects. Currently, `ActiveTask` executes a single request and terminates the producer. By introducing a request queue, the agent producer can wait for subsequent requests (e.g., user input after pausing) without restarting the entire task lifecycle.

Per instructions, the queue must be `AsyncQueue` (imported from `event_queue.py`), and its lifecycle must be managed by `_run_consumer` upon detecting a terminal state.

## 2. Component Modifications

### `src/a2a/server/agent_execution/active_task.py`
**1. Initialization**:
Add a request queue to `ActiveTask` using the compatibility-wrapped `AsyncQueue` from the events module:
```python
from a2a.server.events.event_queue import AsyncQueue

class ActiveTask:
    def __init__(...):
        ...
        # Using AsyncQueue for version-agnostic shutdown() support
        self._request_queue: AsyncQueue[RequestContext] = AsyncQueue()
```

**2. Exposing a way to enqueue requests**:
Add an `enqueue_request` method to allow external components to send follow-up requests.
```python
    async def enqueue_request(self, request: RequestContext) -> None:
        await self._request_queue.put(request)
```
*Note*: `start()` should push the initial request to the queue to maintain backwards compatibility and trigger the producer:
```python
    async def start(self, request: RequestContext, ...):
        ...
        await self._request_queue.put(request)
        # start tasks...
```

**3. `_run_producer` Loop**:
Change `_run_producer` to loop over the queue. It will block on `get()` until a request arrives or the queue is shut down. It now includes a `finally` block to ensure the request queue is shut down if the producer exits prematurely (e.g., due to an unhandled exception or cancellation).
```python
    async def _run_producer(self) -> None:
        logger.debug('Producer[%s]: Started', self._task_id)
        try:
            try:
                while True:
                    # Blocks until a request is available or shutdown() is called
                    request = await self._request_queue.get()
                    try:
                        await self._agent_executor.execute(request, self._event_queue)
                    finally:
                        self._request_queue.task_done()
            except QueueShutDown:
                logger.debug('Producer[%s]: Request queue shut down', self._task_id)
            except asyncio.CancelledError:
                logger.debug('Producer[%s]: Cancelled', self._task_id)
                raise
            except Exception as e:
                logger.exception('Producer[%s]: Failed', self._task_id)
                self._exception = e
            finally:
                # Robustness: Ensure the request queue is shut down if the producer exits.
                # This unblocks any external callers potentially waiting on queue operations.
                self._request_queue.shutdown(immediate=True)

                # Notify waiters that an exception might be set or execution stopped.
                async with self._state_changed:
                    self._state_changed.notify_all()

                # Signal the consumer to wind down.
                await self._event_queue.close(immediate=False)
        finally:
            logger.debug('Producer[%s]: Completed', self._task_id)
```

**4. `_run_consumer` Shutdown Logic**:
When `_run_consumer` processes an event and determines the task is in a terminal state, it must shut down the request queue to unblock the producer loop.
```python
    async def _run_consumer(self) -> None:
        try:
            while True:
                ...
                if is_terminal:
                    ...
                    self._is_finished.set()
                    self._request_queue.shutdown(immediate=True)
                ...
        finally:
            self._is_finished.set()
            # CRITICAL: Always shut down request queue on consumer exit to prevent deadlocks
            self._request_queue.shutdown(immediate=True)
            ...
```

## 3. Concurrency Safety & Race Condition Analysis

### Deadlock Scenario 1: Consumer crashes or exits prematurely
- **Problem**: If `_run_consumer` encounters an unhandled exception and exits, `_run_producer` might be blocked indefinitely waiting on `await self._request_queue.get()`.
- **Solution**: The `finally` block in `_run_consumer` MUST call `self._request_queue.shutdown(immediate=True)`. This ensures that no matter how the consumer dies, the producer is reliably unblocked via a `QueueShutDown` exception.

### Race Condition 1: Fast Producer
- **Scenario**: The agent finishes execution and returns from `_agent_executor.execute()` before the consumer has processed the terminal event.
- **Analysis**: The producer will loop and call `await self._request_queue.get()`. This is safe because it will block. A few milliseconds later, the consumer will process the terminal event, call `self._request_queue.shutdown()`, which will raise `QueueShutDown` inside the producer's `get()`, allowing it to exit cleanly. 

### Race Condition 2: Slow Producer
- **Scenario**: The consumer reads a terminal event and shuts down the request queue while the producer is still executing cleanup code inside `_agent_executor.execute()`.
- **Analysis**: Safe. The queue shutdown does not interrupt the running `execute()` task. Once `execute()` completes naturally, the `while` loop cycles back to `await self._request_queue.get()`, which will immediately raise `QueueShutDown`.

### Cancellation
- **Scenario**: `ActiveTask.cancel()` is called.
- **Analysis**: `_producer_task.cancel()` injects a `CancelledError` into the producer. The producer's internal `finally` block now explicitly calls `self._request_queue.shutdown(immediate=True)`. This ensures that even if the consumer is slow to reach its own shutdown logic, the request queue is immediately closed, and the entire system enters a clean teardown state.

## 4. Alternatives Considered

**Alternative 1: Producer uses an `asyncio.Event` to check for termination**
- *Description*: Instead of relying on `Queue.shutdown()`, the consumer sets an `asyncio.Event` (`self._is_terminal`), and the producer uses `asyncio.wait([request_queue.get(), terminal_event.wait()])`.
- *Analysis*: Highly complex and prone to subtle race conditions (e.g., ensuring tasks are cancelled properly, dealing with multiple return values from `wait`).
- *Verdict*: **Rejected**. `Queue.shutdown()` is specifically designed for this concurrency pattern and provides a thread-safe, robust signal.

**Alternative 2: Producer checks `is_terminal` internally**
- *Description*: The producer checks the database or a flag after `execute()` returns, and shuts down its own queue.
- *Analysis*: Violates the single source of truth. The consumer is the component processing the definitive stream of state changes. Having the producer make state decisions introduces race conditions with the database and consumer.
- *Verdict*: **Rejected**. As requested, the consumer must trigger the shutdown.

**Alternative 3: Using `EventQueue` for requests**
- *Description*: Using the existing `EventQueue` class for requests.
- *Analysis*: `EventQueue` is designed for a pub/sub model with taps and sinks. A request queue is strictly 1-to-1 (external caller -> producer).
- *Verdict*: **Rejected** per explicit user instruction (`not EventQueue`). It introduces unnecessary overhead and complexity.
