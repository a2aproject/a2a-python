# Design: Owner Scoping for Push Notifications

**Status**: Draft
**Owner**: TBD (assign before review)
**Last updated**: 2026-04-23
**Related code**:
- `src/a2a/server/tasks/base_push_notification_sender.py`
- `src/a2a/server/tasks/push_notification_config_store.py`
- `src/a2a/server/tasks/inmemory_push_notification_config_store.py`
- `src/a2a/server/tasks/database_push_notification_config_store.py`
- `src/a2a/server/tasks/task_store.py`
- `src/a2a/server/tasks/inmemory_task_store.py`
- `src/a2a/server/tasks/database_task_store.py`
- `src/a2a/server/request_handlers/default_request_handler.py`
- `src/a2a/server/owner_resolver.py`

## 1. Problem

`BasePushNotificationSender` accepts a `ServerCallContext` at construction time
(`base_push_notification_sender.py:30`) and uses it at dispatch time to call
`PushNotificationConfigStore.get_info(task_id, context)`. But the sender is a
process-wide singleton — push notifications fire **after** the originating
request has completed, so no live request context is available when it
constructs.

External consumers work around this by passing a freshly-constructed
`ServerCallContext()`. That dummy context has
`user = UnauthenticatedUser()`, whose `user_name` is `''`
(`src/a2a/auth/user.py:30`). The default `OwnerResolver` returns
`context.user.user_name` (`owner_resolver.py:13`), so the resolved owner is
`''`. `get_info` then keys into the per-owner bucket for `''`, finds nothing,
and silently returns `[]`. **Notifications are dropped with no error.**

The deeper bug is conceptual: even if a real context were available at dispatch
time, it would be the **dispatcher's** context — the user whose action caused
the event — which is the wrong identity for config lookup.

## 2. Two roles

For any push notification flow there are two distinct users:

| Role | Definition | Context available |
|---|---|---|
| **Registrar** | Called `tasks/pushNotificationConfig/set` | At registration only |
| **Dispatcher** | Triggered the event | At dispatch (live request) |

**Worked example.** Alice registers a webhook for task #12345. Bob later sends
a message that completes the task. Alice (registrar) should receive the
notification — that is what she subscribed to. Bob (dispatcher) is irrelevant
to the lookup. Looking up configs under Bob's owner returns `[]`.

## 3. Authorization model: scoping asymmetry

User-facing operations and the internal dispatch operation have **different
security models** because they run in different lifecycle phases. This
asymmetry is the central design decision of this doc; everything in §5
(interface changes) is the type-level enforcement of it.

| Operation | Caller | Context available | Scoped? | Where the scoping happens |
|---|---|---|---|---|
| `set_info` | end user via request handler | yes (live) | yes | **Handler layer**: `default_request_handler.py:528-530` calls `task_store.get(task_id, context)` and rejects if the task is not visible to the caller. **Store layer**: also partitions storage by owner. |
| `delete_info` | end user via request handler | yes (live) | yes | Store layer: a user can only delete configs in their own owner partition. |
| `get_info` (user-callable read) | end user via request handler (`tasks/pushNotificationConfig/get`, `.../list`) | yes (live) | yes | Store layer: returns only configs in the caller's owner partition. **This is the only confidentiality boundary** — the handler does not filter independently; it returns whatever `get_info` returns (`default_request_handler.py:564-565`, `:644-646`; `default_request_handler_v2.py:379-380`, `:429-431`). |
| `get_info_for_dispatch` (internal) | dispatch path (consumer loop) | no | **no** | Authorization already happened at registration; dispatch fires every registered webhook for the task. |

**Rule: authorization happens at registration, not at dispatch.** When
`set_info` succeeds, the user has been authorized to receive notifications
for that task. Re-checking that authorization at dispatch time would (a)
require a context that doesn't exist (the originating request is over) and
(b) check the wrong identity (the dispatcher, not the registrar — see §2).

### 3.1 Where the primary authorization boundary lives

The primary boundary that prevents Bob from registering a webhook against
Alice's task is **at the handler layer**, not in `PushNotificationConfigStore`.
`on_create_task_push_notification_config`
(`default_request_handler.py:528-530`) does:

```python
task: Task | None = await self.task_store.get(task_id, context)
if not task:
    raise TaskNotFoundError
```

This check delegates authorization to `TaskStore.get`. The shipped
implementations honor the caller's `context` for owner scoping:

- `inmemory_task_store.py:54` — `owner = self.owner_resolver(context)`,
  keys into `self.tasks[owner][task_id]`.
- `database_task_store.py:185-211` — SQL `WHERE owner = :owner AND
  task_id = :task_id`.

So `task_store.get("alice's-task", bob_context)` returns `None`, the
handler raises `TaskNotFoundError`, and `set_info` is never reached.

**Caveat**: the `TaskStore` ABC does not *require* implementations to scope
by `context`. A custom `TaskStore` that ignored its `context` argument
would defeat this check. That contract is a property of `TaskStore`, not
of `PushNotificationConfigStore`, and is out of scope for this design — but
worth being explicit about, because it is the load-bearing assumption that
makes the read-side change in §5 safe.

### 3.2 Role of store-layer owner scoping

Store-layer owner scoping inside `PushNotificationConfigStore` plays
**different roles** for different operations:

- For **`set_info`** it is bookkeeping/defense-in-depth. The handler-layer
  check in §3.1 is the primary authorization; store-layer partitioning
  backstops it.
- For **`delete_info`** it is the primary boundary that lets one
  registrar's delete not affect another registrar's configs on the same
  shared task.
- For **`get_info` (user-callable)** it is **the only confidentiality
  boundary** between users sharing access to the same task. The handlers
  for `tasks/pushNotificationConfig/get` and `.../list` first call
  `task_store.get(task_id, context)` to verify the caller can see the
  task at all, and then return whatever `get_info` returns. They do not
  filter independently. If `get_info` were to return cross-owner data on
  these paths, Bob would be able to read Alice's webhook URLs and
  notification tokens for any task he and Alice both have access to.
- For **`get_info_for_dispatch` (internal)** scoping is intentionally
  absent. Authorization already happened at registration; dispatch fires
  every registered webhook for the task. The dispatch path has no
  user-facing wire endpoint.

This is why §5 introduces a **separate** method for the dispatch path
rather than removing `context` from `get_info`. The user-callable read
path must keep its existing scoping; the dispatch path needs unscoped
reads. Two operations, two methods, two type signatures, two
authorization models.

## 4. Goals / Non-goals

**Goals**
- Deliver push notifications correctly in multi-user deployments.
- Eliminate the dummy-`ServerCallContext` anti-pattern.
- Keep the existing write-side scoping (a user can only register/list/delete
  their own configs).

**Non-goals**
- Wire-protocol changes.
- New auth mechanisms (`X-A2A-Notification-Token` is unchanged).
- Per-task or cross-tenant access control beyond today's owner partitioning.

**Deferred (not part of this design, but not ruled out)**
- Per-config "notify only when registrar == dispatcher" filtering. Could be
  added later as an opt-in flag on `TaskPushNotificationConfig` without
  conflicting with this design.

## 5. Design

### 5.1 Add a separate read path for dispatch; leave the user-callable read
### path alone

The owner is **already** stored alongside every config today (the in-memory
store keys configs by owner; the DB row has an `owner` column at
`models.py:149`). No data-model or write-path change is needed.

The change is to **add** a second read method on `PushNotificationConfigStore`
specifically for the dispatch path:

- `get_info(task_id, context)` — **unchanged.** Owner-scoped. Used by
  `tasks/pushNotificationConfig/get` and `.../list`. Continues to be the
  confidentiality boundary for those endpoints (§3.2).
- `get_info_for_dispatch(task_id)` — **new.** No context. Returns every
  config registered for the task, across all owners. Used only by
  `BasePushNotificationSender.send_notification`. Has no wire endpoint.

This split encodes the §3 asymmetry in the type system: the user-callable
method requires a context and scopes by it; the dispatch method takes no
context and explicitly does not scope. A custom store implementer cannot
conflate them, and a future contributor cannot accidentally reach for the
unscoped method from a user-facing handler — the name says what it's for.

### 5.2 Interface changes

```python
class PushNotificationConfigStore(ABC):
    @abstractmethod
    async def set_info(
        self,
        task_id: str,
        notification_config: TaskPushNotificationConfig,
        context: ServerCallContext,
    ) -> None: ...

    @abstractmethod
    async def get_info(
        self,
        task_id: str,
        context: ServerCallContext,
    ) -> list[TaskPushNotificationConfig]:
        """User-callable read. Returns only configs owned by the caller.

        Backs `tasks/pushNotificationConfig/get` and `.../list`. Owner
        scoping here is the confidentiality boundary between users
        sharing access to the same task — see §3.2.
        """

    @abstractmethod
    async def get_info_for_dispatch(
        self,
        task_id: str,
    ) -> list[TaskPushNotificationConfig]:
        """Internal read used by the push-notification dispatch path.

        Returns every config registered for `task_id`, across all owners.
        Authorization happened at registration (`set_info`); dispatch
        fires every registered webhook. Must not be exposed via any user
        wire endpoint.
        """

    @abstractmethod
    async def delete_info(
        self,
        task_id: str,
        context: ServerCallContext,
        config_id: str | None = None,
    ) -> None: ...
```

Only `get_info_for_dispatch` is new; `set_info`, `get_info`, and
`delete_info` retain their existing signatures.

```python
class BasePushNotificationSender(PushNotificationSender):
    def __init__(
        self,
        httpx_client: httpx.AsyncClient,
        config_store: PushNotificationConfigStore,
    ) -> None:
        self._client = httpx_client
        self._config_store = config_store

    async def send_notification(
        self, task_id: str, event: PushNotificationEvent
    ) -> None:
        configs = await self._config_store.get_info_for_dispatch(task_id)
        ...
```

The sender no longer holds a `ServerCallContext`.
`PushNotificationSender.send_notification` signature on the ABC is
unchanged.

This makes `BasePushNotificationSender` fully stateless w.r.t. caller
identity. Any future per-call concerns (e.g., per-call HTTP middleware,
auth headers tied to a specific request) **must** be threaded through
`send_notification` parameters or transport configuration — not
re-introduced via constructor injection, which would resurrect the same
lifecycle-mismatch bug this design fixes.

### 5.3 End-to-end flow

1. Alice calls `set_info(task_id, cfg, alice_context)` → store records
   owner `alice` against task `12345`.
2. Alice later calls `tasks/pushNotificationConfig/list` for task `12345`
   → handler calls `get_info("12345", alice_context)` → returns `[cfg]`
   (owner-scoped).
3. Bob calls the same endpoint for the same task → handler calls
   `get_info("12345", bob_context)` → returns `[]` (Alice's config is in
   the `alice` partition, not the `bob` partition). Bob never sees
   Alice's URL or token.
4. Bob sends a message; agent completes the task; consumer loop calls
   `push_sender.send_notification("12345", event)` (`active_task.py:505`,
   `default_request_handler.py:344` — both already pass no context).
5. Sender calls `config_store.get_info_for_dispatch("12345")` → returns
   `[cfg]` (cross-owner; Alice's config is included).
6. POST to Alice's URL with Alice's token.

## 6. Alternatives considered

- **Pass dispatcher context to `send_notification`.** Rejected: the
  dispatcher's identity is the wrong key. In the Alice/Bob case it returns
  `[]` and Alice's notification is dropped.
- **Per-`ActiveTask` sender constructed with the live context.** Rejected:
  same correctness problem, plus tightly couples the registry to a concrete
  sender class (or forces a factory abstraction for no semantic gain).
- **Remove `context` from `get_info` entirely (single unscoped read
  method).** Rejected: `get_info` is also called by
  `tasks/pushNotificationConfig/get` and `.../list`
  (`default_request_handler.py:564-565`, `:644-646`;
  `default_request_handler_v2.py:379-380`, `:429-431`), and those handlers
  do not filter independently — they return whatever `get_info` returns.
  A single unscoped method would turn the user-callable list endpoint into
  a cross-tenant disclosure of webhook URLs and tokens for any task two
  users share access to. Splitting into `get_info` and
  `get_info_for_dispatch` (§5.2) preserves the scoped read for user paths
  and isolates the unscoped read for dispatch.
- **Keep one `get_info(task_id, context)` and pass a sentinel "system"
  context from the sender that the store recognizes as "return all."**
  Rejected for the same reason as the `None`-context variant: the type
  signature does not tell a reader (or a custom store implementer) which
  authorization mode is in effect; correctness depends on every
  implementation honoring an unwritten convention. Custom stores can
  silently fail to recognize the sentinel and either leak data or drop
  notifications, with no compile-time signal. The split in §5.2 makes
  the asymmetry a property of the type system rather than a runtime
  convention.
- **Filter in the handler.** Drop `context` from `get_info`, then have
  `on_get_…` and `on_list_…` filter the cross-owner result against the
  caller's resolved owner. Rejected: pulls owner-resolution logic out of
  the store and into the handler, which currently has no knowledge of
  the configured `OwnerResolver`. We'd either have to inject the resolver
  into the handler or expose an `owner_for(context)` method on the store
  — both leak store internals. Future endpoints would have to remember
  to filter; easy to miss.
- **Keep `context` on `get_info` but allow `None`.** Rejected: preserves
  the dummy-context path as "valid," leaves the interface ambiguous, and
  pushes correctness onto caller discipline.

## 7. Migration

### 7.1 Behavioral change

For multi-user deployments this changes runtime behavior: notifications that
were previously dropped will now fire. This is a correctness fix, not a
regression, but it must be called out prominently in the changelog.

### 7.2 Interface changes

| Symbol | Change |
|---|---|
| `PushNotificationConfigStore.get_info` | unchanged |
| `PushNotificationConfigStore.get_info_for_dispatch` | **new** abstract method |
| `PushNotificationConfigStore.set_info` | unchanged |
| `PushNotificationConfigStore.delete_info` | unchanged |
| `BasePushNotificationSender.__init__` | `context` parameter removed |
| `BasePushNotificationSender.send_notification` | now calls `get_info_for_dispatch` instead of `get_info` |
| `PushNotificationSender.send_notification` | unchanged |

Adding a new abstract method is a breaking change for any custom
`PushNotificationConfigStore` implementation: subclasses will fail to
instantiate until they implement `get_info_for_dispatch`. Removing
`context` from `BasePushNotificationSender.__init__` is breaking for
direct callers that pass a context (most notably the dummy-context
pattern in `itk/main.py:339-343`).

**Downstream `get_info` implementations are unchanged** — they keep
filtering by context as before. Custom stores must add a
`get_info_for_dispatch` implementation that returns cross-owner results.
Flag this explicitly in migration notes; a custom store that implements
`get_info_for_dispatch` as `get_info(task_id, dummy_context)` would
silently drop notifications (the original bug, exactly).

### 7.3 Steps

Steps 1–5 must land as a **single commit**. Splitting them leaves the
codebase in an un-buildable intermediate state: adding the new abstract
method alone breaks every concrete subclass at instantiation time; adding
implementations alone leaves the ABC missing the method. Tests (step 6)
and migration notes (step 8) may land in the same commit or follow-ups.

1. Add abstract `get_info_for_dispatch(task_id) -> list[...]` to
   `PushNotificationConfigStore`.
2. Implement `InMemoryPushNotificationConfigStore.get_info_for_dispatch`:
   iterate owner buckets and concatenate matching `task_id` configs (see
   §8.1).
3. Implement `DatabasePushNotificationConfigStore.get_info_for_dispatch`:
   query `WHERE task_id = :task_id` (no owner predicate).
4. `BasePushNotificationSender`: remove `context` parameter and
   `_call_context` field; switch the dispatch call site from
   `get_info(task_id, context)` to `get_info_for_dispatch(task_id)`.
5. Update `itk/main.py` and any other call sites that construct
   `BasePushNotificationSender` to drop the `context` argument.
6. Update tests:
   - `tests/server/tasks/test_push_notification_sender.py`
   - `tests/server/tasks/test_inmemory_push_notifications.py`
   - `tests/server/tasks/test_database_push_notification_config_store.py`
   - `tests/server/request_handlers/test_default_request_handler.py`
   - `tests/server/request_handlers/test_default_request_handler_v2.py`
   - `tests/server/agent_execution/test_active_task.py`
   - `tests/e2e/push_notifications/agent_app.py`
7. Add new tests (§9).
8. Add migration note under `docs/migrations/`.

### 7.4 Downstream surface audit

Required before approval: search public consumers for custom
`PushNotificationConfigStore` and `PushNotificationSender` implementations
and for direct callers of `BasePushNotificationSender(...)`. For each:

- **Custom `PushNotificationConfigStore`**: identify whether they will
  need a non-trivial `get_info_for_dispatch` implementation (i.e., do
  they actually partition by owner today, or are they single-tenant and
  could implement it as a pass-through to the same query without an
  owner predicate?).
- **Direct `BasePushNotificationSender(...)` callers**: identify those
  passing a non-dummy context (if any), and check whether they have
  expectations that this design breaks.
- **Custom `PushNotificationSender`**: confirm none rely on the sender
  holding a `ServerCallContext` (the ABC doesn't expose one, so this
  should be empty).

Outcome must be recorded here before this leaves Draft.

## 8. Detailed considerations

### 8.1 In-memory store: `get_info_for_dispatch` implementation

```python
async def get_info_for_dispatch(
    self, task_id: str
) -> list[TaskPushNotificationConfig]:
    async with self.lock:
        results: list[TaskPushNotificationConfig] = []
        for owner_infos in self._push_notification_infos.values():
            results.extend(owner_infos.get(task_id, []))
        return results
```

Cost is O(number_of_owners). A secondary index keyed by `task_id` can be
added if profiling shows it matters; not in the initial implementation.

`get_info` is unchanged — it remains owner-scoped via
`self.owner_resolver(context)` and a single-owner bucket lookup.

Pre-existing bug: `set_info`
(`inmemory_push_notification_config_store.py:46-47`) mutates
`self._push_notification_infos` outside `self.lock`. Out of scope here;
flag in a follow-up issue.

### 8.2 Database store: `get_info_for_dispatch` query

The new method runs:

```sql
SELECT * FROM push_notification_configs WHERE task_id = :task_id;
```

`task_id` is part of the composite primary key (`models.py:146`), so it
is indexed. The new query does not require a new index. The `owner`
column and its index (`models.py:149`) are retained — `get_info`,
`set_info`, and `delete_info` all continue to filter by owner.

### 8.3 Security

- **Registration-time authorization (unchanged)**: the handler-layer
  `task_store.get(task_id, context)` check
  (`default_request_handler.py:528-530`) prevents a user from registering
  a webhook against a task they do not have access to. See §3.1.
- **Confidentiality of webhook URLs and tokens on user-callable reads
  (unchanged)**: `get_info` keeps its `context` parameter and continues
  to filter by the caller's owner partition. The handlers for
  `tasks/pushNotificationConfig/get` (`default_request_handler.py:564-565`)
  and `.../list` (`default_request_handler.py:644-646`) call into
  `get_info(task_id, context)` and return what it returns. Bob cannot
  enumerate or fetch Alice's configs by ID, even for a task they both
  have access to. The same applies to the v2 handler call sites.
- **Integrity**: the dispatcher cannot inject an arbitrary URL; URLs only
  come from previously-registered configs, each of which passed the
  registration-time authorization check.
- **Webhook auth**: receivers verify `X-A2A-Notification-Token` against
  the token they registered. Unchanged — the token is stored with the
  config.
- **`get_info_for_dispatch` returns cross-owner data, but is not
  user-callable.** It returns every config for a `task_id` regardless of
  owner, including other users' tokens. This is safe because:
  - It has no `tasks/...` wire endpoint; it is only invoked internally by
    `BasePushNotificationSender.send_notification`.
  - Registration was already authorized (§3.1); every config returned
    represents an authorized subscription.
  - The data is not more sensitive than what the dispatch path is about
    to send out over HTTP anyway.

  The method's name signals its trust level. New code that wants to read
  push configs for a user-facing purpose should call `get_info`, not
  `get_info_for_dispatch`. Reviewers should treat any new call site of
  `get_info_for_dispatch` outside the dispatch path as a red flag.

  Conventional caution still applies: the dispatch path should not log
  full configs at info level or otherwise echo them outside the sender.
- **Behavior change**: a notification fires regardless of which user
  triggered the event. This is the intended subscription semantic
  (§3 — registration *is* the authorization). Per-config
  dispatcher-identity filtering is deferred (see §4).

### 8.4 Listing endpoint scope

The handlers for `tasks/pushNotificationConfig/list`
(`default_request_handler.py:627-650`,
`default_request_handler_v2.py:416-435`) implement listing by:

1. Calling `task_store.get(task_id, context)` to verify the caller has
   access to the task.
2. Returning the result of `get_info(task_id, context)`.

The handlers do not perform owner filtering of their own — the
confidentiality of the listed configs comes entirely from `get_info`'s
owner scoping (§3.2). This is why §5 keeps `get_info` scoped and
introduces `get_info_for_dispatch` as a separate method, rather than
removing `context` from `get_info`. Removing `context` would make
`tasks/pushNotificationConfig/list` a cross-tenant disclosure of webhook
URLs and notification tokens for any task two users share access to.

## 9. Test plan

### 9.1 Dispatch correctness

1. **Regression for the dummy-context bug.** Use a `ServerCallContext`
   with a real authenticated `User(user_name="alice")`; register a config
   via `set_info` with that context; construct
   `BasePushNotificationSender` (no context, the new constructor);
   dispatch an event; assert the POST is sent. **Must fail on `main`**
   because the current `BasePushNotificationSender` constructor requires
   a context and the runtime path resolves to owner `''`. Must pass after
   the change. The test must use the default `OwnerResolver`
   (`resolve_user_scope`) to exercise the real failure mode.
2. **Multi-registrar fan-out** (in-memory and DB stores). Users A, B, C
   register distinct webhooks for the same task; one event fires; assert
   all three URLs receive the POST with their respective tokens. Exercises
   `get_info_for_dispatch`.
3. **Cross-user dispatch**. A registers; B triggers an event; assert A
   receives the notification. End-to-end through the consumer loop.
4. **DB query shape for dispatch**. Insert configs under different owners;
   assert `get_info_for_dispatch` returns all of them for the same
   `task_id`.

### 9.2 Cross-tenant isolation on user-callable read paths

These tests guard against the regression that motivated the
`get_info` / `get_info_for_dispatch` split. They must pass both before
and after the change; they fail under the rejected
"single-unscoped-`get_info`" alternative.

5. **`tasks/pushNotificationConfig/list` is owner-scoped.** Set up a task
   accessible to both Alice and Bob (e.g., a `TaskStore` fixture that
   returns the task for both). Alice registers a webhook. Bob calls
   `on_list_task_push_notification_configs(task_id, bob_context)`. Assert
   the response is empty — Bob does not see Alice's URL or token. Repeat
   with both Alice and Bob registering distinct webhooks; assert each
   sees only their own.
6. **`tasks/pushNotificationConfig/get` is owner-scoped.** Alice
   registers a config with a known `config_id`. Bob calls
   `on_get_task_push_notification_config(task_id, config_id, bob_context)`.
   Assert `TaskNotFoundError` is raised — Bob cannot retrieve Alice's
   config even by knowing its ID.
7. Repeat tests 5 and 6 against `default_request_handler_v2`.

### 9.3 Write-side isolation (unchanged behavior, regression guard)

8. **`delete_info` owner isolation preserved**. A registers; B calls
   `delete_info(task_id, bob_context, config_id=A's_config_id)`. Assert
   it is a no-op and A's config remains retrievable via
   `get_info(task_id, alice_context)` and via `get_info_for_dispatch`.

## 10. Rollout

1. Land without a feature flag. For correctly-configured single-user
   deployments behavior is unchanged; for multi-user deployments,
   notifications that were silently dropped will now fire — this is the
   fix.
2. Document the breaking interface change (new abstract method
   `get_info_for_dispatch`; `BasePushNotificationSender.__init__` no
   longer takes `context`) and the behavioral change in the next
   release's changelog.
3. Add a one-page migration note under `docs/migrations/` covering:
   - Custom `PushNotificationConfigStore` implementations (must implement
     `get_info_for_dispatch`).
   - Direct callers of `BasePushNotificationSender(...)` (drop the
     `context` argument).
   - Custom `PushNotificationSender` implementations (no change required;
     ABC unchanged).
   - The trap: implementing `get_info_for_dispatch` as
     `get_info(task_id, dummy_context)` reproduces the original bug.

## 11. Open questions

1. Result of the §7.4 downstream audit.
