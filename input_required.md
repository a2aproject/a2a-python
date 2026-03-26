# Analysis of `test_workflow_input_required` Bug

## Bug Overview

The `test_workflow_input_required[default_handler]` integration test simulates a multi-turn agent workflow:
1.  **Turn 1:** The user sends a start message. The agent executes, transitions to `TASK_STATE_WORKING`, and then pauses, emitting a `TASK_STATE_INPUT_REQUIRED` state.
2.  **Turn 2:** The user sends a follow-up message to the same task to provide the required input. The agent should resume, transition back to `TASK_STATE_WORKING`, and finally complete with `TASK_STATE_COMPLETED`.

When using the `DefaultRequestHandler` (which relies on `ActiveTask`), Turn 2 fails. During the initial triage, two distinct errors were observed:
1.  **`TaskAlreadyStartedError`**: Raised because `ActiveTask.start()` prevents a task from being started if `_producer_task` is already set.
2.  **`InvalidParamsError`**: Raised because `TaskManager` expects the `context_id` of the incoming message to match the `context_id` of the original task, but a new `context_id` was generated.

## Root Causes

The bug stems from a combination of lifecycle mismatches between `ActiveTask`, `EventQueue`, and the `DefaultRequestHandler` logic:

### 1. Context ID Generation on Resumption
When the second message is sent without an explicit `context_id`, the `DefaultRequestHandler` attempts to build the `RequestContext`. Because it doesn't fetch the existing task's `context_id` at this early stage, the `SimpleRequestContextBuilder` generates a brand new `context_id`. Later, when `TaskManager` processes the event, it throws an `InvalidParamsError` because the new `context_id` doesn't match the original one stored in the database.

### 2. ActiveTask Single-Turn Assumption
`ActiveTask` was designed with the assumption that a task executes once. When `ActiveTask.start()` is called on Turn 2, it checks if `self._producer_task is not None`. Since the `ActiveTask` is kept alive in the `ActiveTaskRegistry` (to serve existing subscribers), it still holds the completed `_producer_task` from Turn 1, leading it to immediately raise a `TaskAlreadyStartedError`.

### 3. EventQueue Closure by AgentExecutor
In `ActiveTask._run_producer`, the `AgentExecutor.execute` method is awaited, and then the `event_queue` is explicitly closed in a `finally` block (or by the agent itself). When a task reaches `TASK_STATE_INPUT_REQUIRED`, the agent finishes its current execution turn and closes the queue. This tears down the queue, forcing the `ActiveTask` consumer to exit and marking the task as finished, severing any active SSE subscriber streams prematurely. The A2A protocol implies that a task's event stream should remain open across interruptions until a terminal state (like `COMPLETED` or `CANCELED`) is reached.

---

## Options for Fixing the Bug

Here are several architectural approaches to fix this issue, ranging from localized state management to broader lifecycle changes:

### Option 1: The Proxy & Turn-Reset Approach (Recommended)
This approach keeps a single `ActiveTask` and `EventQueue` alive for the entire lifespan of the task, allowing subscribers to stay connected across interruptions.

*   **Context ID Fix:** Update `DefaultRequestHandler._setup_active_task` to fetch the existing task from the database (using `task_id`) and reuse its `context_id` before building the `RequestContext`.
*   **Queue Proxy:** Pass an `_AgentEventQueueProxy` to the `AgentExecutor` that intercepts and ignores `.close()` calls from the agent. 
*   **Lifecycle Management:** Move the responsibility of closing the queue to the `ActiveTask` consumer. The consumer will only physically close the queue when a terminal state (e.g., `TASK_STATE_COMPLETED`, `TASK_STATE_CANCELED`) is reached.
*   **Producer Restart:** Modify `ActiveTask.start()` to allow starting a new producer if the existing `_producer_task` is `done()`. It must reset turn-specific state (like `_result_available` and `_exception`) before spawning the new producer.

*(Note: Care must be taken to handle cancellation correctly, ensuring that `cancel()` calls still result in the queue being closed by emitting the `TASK_STATE_CANCELED` state).*

### Option 2: Ephemeral ActiveTask (Recreate on Resume)
Redefine `ActiveTask` to represent a *single execution turn* rather than the entire task lifespan.

*   When the agent yields `TASK_STATE_INPUT_REQUIRED`, the `ActiveTask` consumer finishes, the queue closes, and the `ActiveTask` is removed from the `ActiveTaskRegistry`.
*   On Turn 2, `DefaultRequestHandler` naturally creates a new `ActiveTask` and a new `EventQueue`.
*   **Drawback:** This breaks long-lived SSE subscriptions. A client subscribed via `SubscribeToTask` would be disconnected when `INPUT_REQUIRED` is hit and would have to manually re-subscribe on the next turn. The A2A protocol generally expects streams to stay alive until a terminal state.

### Option 3: Queue Reset / Splicing
Allow the `EventQueue` to close at the end of Turn 1, but keep the `ActiveTask` alive.

*   When `ActiveTask.start()` is called for Turn 2, instantiate a completely new `EventQueue` for the producer.
*   The `ActiveTask` consumer is modified to read from this new queue, effectively "splicing" the new events into the existing subscriber streams.
*   **Drawback:** This adds significant complexity to the `ActiveTask` consumer and event routing logic, as it must dynamically switch its source queue while keeping sink queues alive.

### Option 4: Explicit Turn-Based Executor Interface
Modify the `AgentExecutor` interface to differentiate between ending a *turn* and ending a *task*.

*   Introduce an `execute_turn()` method that does not mandate queue closure upon return.
*   The framework natively understands that the agent is pausing, and `ActiveTask` manages the queue lifecycle exclusively based on the emitted `TaskState` (closing only on terminal states).
*   **Drawback:** Requires refactoring the `AgentExecutor` interface, which might be a breaking change for existing agent implementations.