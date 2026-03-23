# Analysis of Handler Differences

This document provides a detailed analysis of the behavioral differences between the `DefaultRequestHandler` and the `LegacyRequestHandler` observed in integration tests. For each difference, it references the official A2A protocol specification (`knowledge/A2A/docs/specification.md`) to determine the correct and expected behavior.

---

## 1. Canceling Terminal Task

### Difference
When a client attempts to cancel a task that is already in a terminal state (e.g., `TASK_STATE_COMPLETED`), the **Legacy Handler** raises a `TaskNotCancelableError`. The **Default Handler**, however, does not raise an error; instead, it gracefully returns the task in its current terminal state.

### Code Pointers
*   **Test:** `tests/integration/test_handler_comparison.py::test_scenario_7_cancel_terminal_task`
*   **Legacy:** `src/a2a/server/request_handlers/legacy_request_handler.py` (Raises `TaskNotCancelableError` when state is terminal)
*   **Default:** `src/a2a/server/request_handlers/default_request_handler.py` (Returns the task from `task_manager.get_task()` during cancel)

### Specification & Correctness
**The Legacy Handler is correct.** 
According to the A2A Protocol Specification (`Cancel Task` section 3.1.5):
> **Errors:** `TaskNotCancelableError`: The task is not in a cancelable state (e.g., already completed, failed, or canceled).

The Default Handler's behavior is currently non-compliant with the specification, as it swallows the expected error.

---

## 2. Return Immediately (New Task)

### Difference
When a client sends a message with `SendMessageConfiguration(return_immediately=True)` to create a new task:
*   The **Legacy Handler** blocks and waits for the agent to emit its first event (usually transitioning to `TASK_STATE_WORKING`) before returning the HTTP response.
*   The **Default Handler** returns instantaneously with a task state of `TASK_STATE_SUBMITTED`, without waiting for the agent's execution loop to even start.

### Code Pointers
*   **Test:** `tests/integration/test_handler_comparison.py::test_scenario_8_return_immediately_new_task`
*   **Legacy:** Awaits the first item from the queue before responding.
*   **Default:** Intercepts `return_immediately=True` and instantly constructs a `SUBMITTED` Task object.

### Specification & Correctness
**The Default Handler is correct.**
According to the A2A Protocol Specification (Section 3.2.2 `SendMessageConfiguration`):
> **Non-Blocking (`return_immediately: true`)**: The operation MUST return immediately after creating the task, even if processing is still in progress. The returned task will have an in-progress state (e.g., `working`, `input_required`).

The Legacy Handler introduces blocking latency by waiting for the agent's first event, which violates the "return immediately" mandate.

---

## 3. Concurrent Task Execution (Message to Running Task)

### Difference
If a client sends an additional message to a task that is actively executing (`TASK_STATE_WORKING`):
*   The **Legacy Handler** blindly starts a second background producer task for the same `task_id`, causing concurrent execution of the agent logic on the same task.
*   The **Default Handler** strictly enforces single execution. It checks the `ActiveTaskRegistry` and raises a `TaskAlreadyStartedError` (custom error) if a producer is already active for that task.

### Code Pointers
*   **Test:** `tests/integration/test_handler_comparison.py::test_scenario_9_message_to_running_task` and `test_scenario_3_concurrency_double_execution`
*   **Legacy:** Creates background tasks without concurrency locks per `task_id`.
*   **Default:** `src/a2a/server/agent_execution/active_task.py::ActiveTask.start` (Raises `TaskAlreadyStartedError` if `_producer_task` is not `None`).

### Specification & Correctness
**The Default Handler's architecture is vastly safer**, though technically using a non-standard error code.
The specification (Section 3.4 Multi-Turn Interactions) states:
> Agents MAY accept additional messages for tasks in non-terminal states to enable multi-turn interactions...

However, concurrently executing the same agent loop on the exact same task ID usually leads to corrupted state/race conditions (as seen in Legacy). Enforcing mutual exclusion (Default) is correct. Note: `TaskAlreadyStartedError` is an internal Python SDK exception and should ideally be mapped to the spec-compliant `UnsupportedOperationError` at the protocol boundary if concurrent messages are unsupported.

---

## 4. Cancellation Gracefulness and Timeout Handling

### Difference
When a cancellation request is issued, but the agent finishes the task normally (transitions to `COMPLETED`) before acknowledging the cancellation:
*   The **Legacy Handler** raises an exception/timeout because the task didn't transition to the explicit `CANCELED` state. It also waits synchronously for internal queue cleanup, making it prone to timeouts.
*   The **Default Handler** handles this race condition gracefully and simply returns the newly completed task state.

### Code Pointers
*   **Test:** `tests/integration/test_handler_parity_scenarios.py::test_scenario_2_cancel_results_in_completed` and `test_scenario_6_cancellation_calls_agent`

### Specification & Correctness
**The Default Handler is more robust and practically correct.**
In asynchronous distributed systems, race conditions between a task completing naturally and a cancellation request arriving are inevitable. Returning the actual terminal state (even if it's `COMPLETED` rather than `CANCELED`) provides an accurate, graceful reflection of the task's final status rather than throwing an artificial timeout/error.

---

## 5. Startup Error Propagation on Non-Blocking Requests

### Difference
When a client requests a non-blocking execution (`return_immediately=True`), and the agent throws an exception immediately during its startup phase:
*   The **Legacy Handler** propagates this exception synchronously back to the caller in the `send_message` response (because it waits for the first event).
*   The **Default Handler** successfully returns a `SUBMITTED` task synchronously. The startup exception occurs in the background and sets the task to a `FAILED` state asynchronously, which the client will discover via later polling or streaming.

### Code Pointers
*   **Test:** `tests/integration/test_handler_parity_scenarios.py::test_scenario_10_error_before_nonblocking`

### Specification & Correctness
**The Default Handler is correct.**
According to the spec for `return_immediately: true`:
> The operation MUST return immediately after creating the task... It is the caller's responsibility to poll for updates using Get Task, subscribe via Subscribe to Task, or receive updates via push notifications.

By fully decoupling the HTTP request/response cycle from the agent's execution loop, the Default Handler strictly honors the non-blocking requirement. Background failures should indeed be discovered through subsequent polling/streaming, not synchronous HTTP 500s.
