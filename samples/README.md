# A2A Python SDK — Samples

This directory contains runnable examples demonstrating how to build and interact with an A2A-compliant agent using the Python SDK.

## Contents

| File | Role | Description |
|---|---|---|
| `hello_world_agent.py` | **Server** | A2A agent server |
| `cli.py` | **Client** | Interactive terminal client |
| `agent_card_signing.py` | **Server + Client** | Signing and verifying an Agent Card |

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

---

## `agent_card_signing.py` — Agent Card Signing

An Agent Card is fetched before any trust has been established with the agent, so everything in it — transport URLs, security schemes, skills — is attacker-controlled until it is verified. This sample signs a card on the server and verifies it on the client.

Demonstrates:
- Signing the Agent Card with an ES256 key via `create_agent_card_signer`, wired into the card endpoint through `create_agent_card_routes(card_modifier=...)`
- Publishing the matching public key as a JWKS document at `/.well-known/jwks.json`, referenced by the signature's `jku` header
- Verifying the card with `create_signature_verifier`, passed to `A2ACardResolver.get_agent_card(signature_verifier=...)` (the same argument is accepted by `ClientFactory.create_from_url`)
- Pinning trust: the verifier accepts keys only from an allowlist of JWKS URLs and only signatures using an allowlisted algorithm

The default `demo` mode starts the server, verifies its card, and then shows the three failures a verifier exists to catch: a card whose transport URL was rewritten in transit, a card with its signature stripped, and a genuine card whose `jku` the client does not trust.

**Run:**

```bash
# Server and client together (default):
uv run python samples/agent_card_signing.py

# Or run the two halves separately:
uv run python samples/agent_card_signing.py serve
uv run python samples/agent_card_signing.py verify --url http://127.0.0.1:41242
```

Requires the `signing`, `encryption` and `fastapi` extras (`pip install 'a2a-sdk[signing,encryption,fastapi]'`); they are already present when working from a `uv sync` of this repo.
