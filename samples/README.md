# A2A Python SDK — Samples

This directory contains runnable examples demonstrating how to build and interact with an A2A-compliant agent using the Python SDK.

## Contents

| File | Role | Description |
|---|---|---|
| `hello_world_agent.py` | **Server** | A2A agent server |
| `cli.py` | **Client** | Interactive terminal client |

The samples are designed to work together out of the box: the agent listens on `http://127.0.0.1:41241`, which is the default URL used by the client.
---

## `hello_world_agent.py` — Agent Server

Implements an A2A agent that responds to simple greeting messages (e.g., "hello", "how are you", "bye") with text replies, simulating a 1-second processing delay.

Demonstrates:
- Subclassing `AgentExecutor` and implementing `execute()` / `cancel()`
- Publishing streaming status updates and artifacts via `TaskUpdater`
- Exposing all three transports in both protocol versions (v1.0 and v0.3 compat) simultaneously:
  - **JSON-RPC** (v1.0 and v0.3) at `http://127.0.0.1:41241/a2a/jsonrpc`
  - **HTTP+JSON (REST)** (v1.0 and v0.3) at `http://127.0.0.1:41241/a2a/rest`
  - **gRPC v1.0** on port `50051`
  - **gRPC v0.3 (compat)** on port `50052`
- Serving the agent card at `http://127.0.0.1:41241/.well-known/agent-card.json`

**Run:**

```bash
uv run python samples/hello_world_agent.py
```

---

## `cli.py` — Client

An interactive terminal client with full visibility into the streaming event flow. Each `TaskStatusUpdate` and `TaskArtifactUpdate` event is printed as it arrives.

Features:
- Transport selection via `--transport` flag (`JSONRPC`, `HTTP+JSON`, `GRPC`)
- Session management (`context_id` persisted across messages, `task_id` per task)
- Graceful error handling for HTTP and gRPC failures

**Run:**

```bash
# Connect to the local hello_world_agent (default):
uv run python samples/cli.py

# Connect to a different URL, using gRPC:
uv run python samples/cli.py --url http://192.168.1.10:41241 --transport GRPC
```

Then type a message like `hello` and press Enter.

Type `/quit` or `/exit` to stop, or press `Ctrl+C`.
