# Cluster-mode sample (multi-replica A2A)

Runs several A2A server replicas that share one durable task store and one event
stream, so any replica can serve any request for any task. This demonstrates the
multi-server support in `a2a.server.cluster`: send, resubscribe, and cancel all
work regardless of which replica a request lands on — no sticky routing needed.

## What it shows

- **Shared, versioned task store** (`VersionedDatabaseTaskStore`) — concurrent
  writes across replicas are serialized by optimistic concurrency (CAS); no lost
  updates.
- **Shared event stream** (`DatabaseTaskEventStream`) — a subscription on one replica
  streams events produced by an agent running on another.
- **Cancel via CAS** — a cancel on replica C stops an agent running on replica A
  (A's next save fails the compare-and-swap and aborts).

## Requirements

Nothing extra by default: it uses a file-backed SQLite database
(`/tmp/a2a_cluster_demo.db`) shared by all replica processes.

For a realistic setup, point every replica at a shared Postgres/MySQL:

```bash
export A2A_CLUSTER_DSN='postgresql+asyncpg://user:pass@localhost/a2a'
```

## Run

Start a 3-replica cluster (ports 41241, 41242, 41243):

```bash
python -m samples.clustermode.run_cluster --replicas 3 --base-port 41241
```

In another terminal, exercise it (one task, spread across replicas):

```bash
python -m samples.clustermode.exercise --host 127.0.0.1 --ports 41241,41242,41243
```

Expected output ends with:

```
OK: one task was started, observed, and cancelled across three different replicas.
```

## Files

| File | Purpose |
|------|---------|
| `cluster_common.py` | Shared store/stream wiring, agent card, and the demo agent |
| `server.py` | One replica (a FastAPI app on one port) |
| `run_cluster.py` | Launches N replicas as subprocesses |
| `exercise.py` | Client that sends/gets/cancels one task across replicas |

## How it maps to the API

Every replica builds its handler the same way — the only multi-server-specific
part is passing a shared `event_stream` and a shared `VersionedTaskStore`:

```python
handler = DefaultRequestHandler(
    agent_executor=SlowEchoAgent(...),
    task_store=VersionedDatabaseTaskStore(engine=..., create_table=False),
    agent_card=agent_card,
    event_stream=DatabaseTaskEventStream(engine=..., create_table=False),
)
```

Omit `event_stream` (and use a plain `InMemoryTaskStore`) and you get the ordinary
single-process behaviour — the multi-server path is fully opt-in.
```
