---
name: task-state-invariants
description: Use when adding or changing code that writes A2A task state (Task, TaskStatus, TaskStatusUpdateEvent, TaskUpdater, task stores, push notifications, cluster event streams, v0.3 compat mappers), or when reviewing such a change — runs the sibling-surface propagation checklist so a state change or field lands on every surface (persisted store, streaming subscribers, push notification, event table, both compat transports) instead of only some of them. Not for style or type rules (read docs/ai/coding_conventions.md) and not the pre-commit gate (read docs/ai/mandatory_checks.md).
---

# Task State Invariants

Implements the missing verification step for the task-lifecycle subsystem:
`AGENTS.md` tells you to run the check sequence, and `evidence_rules.md`
tells you to cite behavior, but neither tells you **which surfaces a task
state change must reach**. Most bugs in this area are not wrong values —
they are a correct value that reached one surface and not the others.

## When to load this skill

Trigger on ANY of these, without waiting for the user to ask:

- You write or mutate `TaskStatus`, `Task`, `TaskStatusUpdateEvent`, or
  `TaskArtifactUpdateEvent`.
- You touch `TaskManager.save_task_event`, a `TaskStore`, a
  `VersionedTaskStore`, or `DatabaseTaskStore` column mapping.
- You touch push notifications, the event queue/consumer, or the cluster
  event stream (`task_events`).
- You add or rename a protocol field, or touch `compat/v0_3/`.
- You transition a task to a terminal or interrupted state anywhere.
- A reviewer comment says some surface "still gets the old value", "keeps
  the stale timestamp", or "is missing on the other transport".

If unsure whether this covers your change: load it. Checking the matrix is
cheap; a half-propagated state ships a bug.

## The invariant set

Each claim is anchored to current `main`. Re-read the file before quoting.

1. **A terminal state must carry `status.timestamp`.**
   `TERMINAL_TASK_STATES` is defined once in `src/a2a/utils/constants.py:35`
   and imported everywhere else — never re-declare a local copy.
   `DatabaseTaskStore` derives `last_updated` from it
   (`src/a2a/server/tasks/database_task_store.py:152-157`), so an unstamped
   terminal state persists `NULL` and silently drops the task from
   `ListTasks` ordering and `status_timestamp_after` filtering.
2. **Stamp the event, then copy — not the copy.**
   `TaskManager.save_task_event` replaces status wholesale with
   `task.status.CopyFrom(event.status)`
   (`src/a2a/server/tasks/task_manager.py:285`). That same event object is
   handed to streaming subscribers, push notifications, and the event table.
   Stamping the local `task` after the copy leaves the event unstamped, so
   the store is fixed and the other three surfaces stay broken.
3. **`CopyFrom` inherits the previous timestamp.** A copy-then-set-state
   transition (for example the cancel path,
   `src/a2a/server/agent_execution/active_task.py:979-982`) reuses whatever
   timestamp the prior state had, so the terminal write looks like a
   `WORKING` update. Set `status.timestamp` explicitly on every transition
   you introduce.
4. **Push delivery is best-effort and never task-affecting.**
   `_dispatch_notification` already contains delivery failures and returns
   a bool; `send_notification` is awaited inline from the consumer at
   `src/a2a/server/agent_execution/active_task.py:410`. Anything that can
   raise there (config-store reads, URL validators, custom senders) must not
   escape into the failure path, or a webhook hiccup rewrites a completed
   task to `FAILED`.
5. **Every request must produce at least one event.** With zero events,
   `on_message_send` falls through to `active_task.get_task()`
   (`src/a2a/server/request_handlers/default_request_handler_v2.py:518-520`),
   which awaits `_task_created` with no timeout
   (`src/a2a/server/agent_execution/active_task.py:1130`) and hangs the
   request, producer, and consumer forever. Guard before awaiting (the
   `on_message_send` caller has done so since #1345), or the fallback
   must be bounded.
6. **Store and stream must agree.** A blocking call may return a shared
   snapshot while later events land on the store — the copy-on-yield caveat
   is documented in-code at
   `src/a2a/server/agent_execution/active_task.py:974-981` (tracked in
   #1175 / #1191). After any change here, assert `GetTask` equals what the
   caller received.
7. **v0.3 compat has two independent mappers.** `compat/v0_3/proto_utils.py`
   serves the gRPC surface and `compat/v0_3/conversions.py` serves JSON-RPC
   and REST. A field mapped in one and not the other appears and disappears
   by transport — see #1276 (status timestamp mapped for JSON-RPC/REST but
   dropped on gRPC). The same split bites error handling rather than fields:
   early failures on v0.3 REST streams had to be fixed separately in #1403.
   Any compat change must touch and test both mappers.
8. **Continuation identity is inferred, not invented.** With only
   `message.task_id`, the SDK must reuse the task's `context_id`
   (A2A v1.0 §3.4.3); generating a fresh one makes events disagree with the
   stored task — see #1206 and the open follow-up #1411.

## Procedure

1. Name the surfaces your change writes. Default assumption: **all five** —
   persisted store, streaming subscribers, push notification, cluster event
   table, v0.3 compat (both mappers).
2. Grep for the sibling call sites before editing:

   ```bash
   grep -rn "save_task_event\|CopyFrom\|send_notification\|enqueue_event" src/a2a
   ```

3. For each surface you touch, add or update a test in the matching file:
   `tests/server/tasks/test_task_manager.py`,
   `tests/server/tasks/test_database_task_store.py`,
   `tests/server/agent_execution/test_active_task.py`,
   `tests/server/tasks/test_push_notification_sender.py`,
   `tests/compat/v0_3/test_proto_utils.py`,
   `tests/compat/v0_3/test_conversions.py`.
4. Assert the **event object** as well as the stored task — not just the
   store — for any timestamp or field you propagate.
5. If you added a field, state in the PR which transports carry it (REST,
   JSON-RPC, gRPC, v0.3 compat each).
6. Then run the normal gate: `docs/ai/mandatory_checks.md`.

## Evidence for the PR

Per `docs/ai/evidence_rules.md`, a behavior claim needs control-flow
evidence. For this subsystem, the minimum is:

- the surface list from step 1, with the file:line of each write;
- a test per surface touched, named for the surface;
- before/after output showing the surface that used to get the old value.

## Anti-patterns

- Fixing the store and assuming the stream follows. It does not — the event
  is published separately.
- Re-declaring `TERMINAL_TASK_STATES` locally instead of importing it from
  `a2a.utils.constants`.
- Testing compat through one transport and concluding the field works.
- Silently returning `None` from an executor: no event means no task, and
  the caller blocks (invariant 5).
- Letting an infrastructure exception (store read, URL validation) escape
  into the consumer failure path (invariant 4).

## Boundaries

- This skill does not replace `mandatory_checks.md`; it runs before that
  gate, not instead of it.
- It does not decide scope. If a surface is intentionally out of scope,
  say so in the PR and link the issue that owns it.
- If you find an invariant here violated in code, load
  `mistake-reflection` only if *you* introduced the error; otherwise report
  it as an issue with reproduction.

## Provenance

Derived from recurring defects in this subsystem and the review feedback on
them: #1313/#1329 (invariant 4), #1330 (5), #1331 (1, 2, 3), #1188 (6),
#1276 (7), #1206/#1411 (8), and community questions #248, #435, #40, #1224.
