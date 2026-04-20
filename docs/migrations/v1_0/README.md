# Migration Guide: v0.3 → v1.0

This guide covers the breaking changes introduced in `a2a-sdk` v1.0 and explains how to update your code.

> **Related guides**: If you use the database persistence layer, also see the [Database Migration Guide](database/).

---

## Table of Contents

1. [Package Dependency](#1-package-dependency)
2. [Types](#2-types)
3. [Server: DefaultRequestHandler](#3-server-defaultrequesthandler)
4. [Server: Application Setup](#4-server-application-setup)
5. [Client: Creating a Client](#5-client-creating-a-client)
6. [Client: Sending Messages & Handling Responses](#6-client-sending-messages--handling-responses)
7. [Client: Push Notifications Config](#7-client-push-notifications-config)
8. [Helper Utilities](#8-helper-utilities)
9. [Import Path Changes (Quick Reference)](#9-import-path-changes-quick-reference)

---

## 1. Package Dependency

Update your dependency to the new version:

```toml
# pyproject.toml — before
dependencies = ["a2a-sdk>=0.3.0"]

# pyproject.toml — after
dependencies = ["a2a-sdk>=1.0.0"]
```

---

## 2. Types

Types are now **Protobuf-based** instead of Pydantic models.


### Enum values: snake_case → SCREAMING_SNAKE_CASE

All enum values have been renamed from snake_case strings to `SCREAMING_SNAKE_CASE`.

This affects every enum in the SDK: `TaskState`, `Role`.

| Enum | v0.3 | v1.0 |
|---|---|---|
| `TaskState` | *(no equivalent — protobuf default)* | `TaskState.TASK_STATE_UNSPECIFIED` |
| `TaskState` | `TaskState.submitted` | `TaskState.TASK_STATE_SUBMITTED` |
| `TaskState` | `TaskState.working` | `TaskState.TASK_STATE_WORKING` |
| `TaskState` | `TaskState.completed` | `TaskState.TASK_STATE_COMPLETED` |
| `TaskState` | `TaskState.failed` | `TaskState.TASK_STATE_FAILED` |
| `TaskState` | `TaskState.canceled` | `TaskState.TASK_STATE_CANCELED` |
| `TaskState` | `TaskState.input_required` | `TaskState.TASK_STATE_INPUT_REQUIRED` |
| `TaskState` | `TaskState.auth_required` | `TaskState.TASK_STATE_AUTH_REQUIRED` |
| `TaskState` | `TaskState.rejected` | `TaskState.TASK_STATE_REJECTED` |
| `Role` | *(no equivalent — protobuf default)* | `Role.ROLE_UNSPECIFIED` |
| `Role` | `Role.user` | `Role.ROLE_USER` |
| `Role` | `Role.agent` | `Role.ROLE_AGENT` |

> **Example**: [`a2a-mcp-without-framework/server/agent_executor.py` in PR #509](https://github.com/a2aproject/a2a-samples/pull/509/changes#diff-1f9b098f9f82ee40666ee61db56dc2246281423c445bcf017079c53a0a05954f)

### Message and Part construction

**Before (v0.3):**
```python
from a2a.types import Message, Part, Role, TextPart
from uuid import uuid4

message = Message(
    role=Role.user,
    parts=[Part(TextPart(text="Hello"))],
    message_id=uuid4().hex,
    task_id=uuid4().hex,
)
```

**After (v1.0):**
```python
from a2a.helpers import new_text_message
from a2a.types import Role

# Use the helper for text messages
message = new_text_message(text="Hello", role=Role.ROLE_USER)

# Or construct directly
from a2a.types import Message, Part
from uuid import uuid4

message = Message(
    role=Role.ROLE_USER,
    parts=[Part(text="Hello")],
    message_id=uuid4().hex,
    task_id=uuid4().hex,
)
```

Key differences:
- `Part(TextPart(text=...))` → `Part(text=...)` (flat union field)
- `Role.user` → `Role.ROLE_USER`, `Role.agent` → `Role.ROLE_AGENT`
- `TextPart` is no longer needed; use `Part(text=...)` directly

> **Example**: [`helloworld/test_client.py` in PR #474](https://github.com/a2aproject/a2a-samples/pull/474/files#diff-f62c07d3b00364a3100b7effb3e2a1cca0624277d3e40da1bdb07bb46b6a8cef)

### AgentCard Structure

The `AgentCard` has been significantly restructured to support multiple transport interfaces.

Key differences:
- `url` is gone; use `supported_interfaces` with one or more `AgentInterface` entries
- `AgentCapabilities.input_modes` and `AgentCapabilities.output_modes` are removed from `AgentCapabilities`; use `AgentCard.default_input_modes` / `AgentCard.default_output_modes` for card-level defaults, or `AgentSkill.input_modes` / `AgentSkill.output_modes` for per-skill overrides
- `supports_authenticated_extended_card` is no longer a top-level `AgentCard` field; it has moved into `AgentCapabilities` and is renamed to `extended_agent_card`
- `AgentInterface.protocol_binding` accepted values: `'JSONRPC'`, `'HTTP+JSON'`, `'GRPC'`
- `examples` field has moved to `AgentSkill.examples` (set it per skill instead)

**Before (v0.3):**
```python
from a2a.types import AgentCard, AgentCapabilities, AgentSkill

agent_card = AgentCard(
    name='My Agent',
    description='...',
    url='http://localhost:9999/',
    version='1.0.0',
    default_input_modes=['text/plain'],
    default_output_modes=['text/plain'],
    supports_authenticated_extended_card=True,
    capabilities=AgentCapabilities(
        input_modes=['text/plain'],
        output_modes=['text/plain'],
        streaming=True,
    ),
    skills=[skill],
    examples=['example'],
)
```

**After (v1.0):**
```python
from a2a.types import AgentCard, AgentCapabilities, AgentInterface, AgentSkill

agent_card = AgentCard(
    name='My Agent',
    description='...',
    supported_interfaces=[
        AgentInterface(
            protocol_binding='JSONRPC',
            url='http://localhost:9999/',
        )
    ],
    version='1.0.0',
    default_input_modes=['text/plain'],
    default_output_modes=['text/plain'],
    capabilities=AgentCapabilities(
        streaming=True,
        extended_agent_card=True,
    ),
    skills=[skill],
)
```

> **Example**: [`a2a-mcp-without-framework/server/__main__.py` in PR #509](https://github.com/a2aproject/a2a-samples/pull/509/files#diff-d15d39ae64c3d4e3a36cc6fb442302caf4e32a6dbd858792e7a4bed180a625ac)

---

## 3. Server: DefaultRequestHandler

### Constructor signature: `agent_card` is now required

`DefaultRequestHandler` now requires `agent_card` as a constructor argument (it was previously passed to the application wrapper).

**Before (v0.3):**
```python
request_handler = DefaultRequestHandler(
    agent_executor=MyAgentExecutor(),
    task_store=InMemoryTaskStore(),
)
```

**After (v1.0):**
```python
request_handler = DefaultRequestHandler(
    agent_executor=MyAgentExecutor(),
    task_store=InMemoryTaskStore(),
    agent_card=agent_card,
)
```

> **Example**: [`a2a-mcp-without-framework/server/__main__.py` in PR #509](https://github.com/a2aproject/a2a-samples/pull/509/files#diff-d15d39ae64c3d4e3a36cc6fb442302caf4e32a6dbd858792e7a4bed180a625ac)

---

## 4. Server: Application Setup

The `A2AStarletteApplication` wrapper class has been removed. Server setup now uses **Starlette route factory functions** directly, giving you full control over the routing.

**Before (v0.3):**
```python
from a2a.server.apps import A2AStarletteApplication
import uvicorn

server = A2AStarletteApplication(
    agent_card=agent_card,
    http_handler=request_handler,
)
uvicorn.run(server.build(), host=host, port=port)
```

**After (v1.0):**
```python
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from starlette.applications import Starlette
import uvicorn

routes = []
routes.extend(create_agent_card_routes(agent_card))
routes.extend(create_jsonrpc_routes(request_handler, rpc_url='/'))

app = Starlette(routes=routes)
uvicorn.run(app, host=host, port=port)
```

If you need REST transport in addition to JSON-RPC:
```python
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes, create_rest_routes
from starlette.applications import Starlette
import uvicorn

routes = []
routes.extend(create_agent_card_routes(agent_card))
routes.extend(create_jsonrpc_routes(request_handler, rpc_url='/'))
routes.extend(create_rest_routes(request_handler))

app = Starlette(routes=routes)
uvicorn.run(app, host=host, port=port)
```

> **Example**: [`a2a-mcp-without-framework/server/__main__.py` in PR #509](https://github.com/a2aproject/a2a-samples/pull/509/files#diff-d15d39ae64c3d4e3a36cc6fb442302caf4e32a6dbd858792e7a4bed180a625ac)

---

## 5. Client: Creating a Client

The `A2AClient` class has been removed. Use the new `create_client()` factory function.

### Simple usage: `create_client()`

**Before (v0.3):**
```python
import httpx
from a2a.client import A2AClient, A2ACardResolver

async with httpx.AsyncClient() as httpx_client:
    resolver = A2ACardResolver(httpx_client, base_url)
    agent_card = await resolver.get_agent_card()
    client = A2AClient(httpx_client, agent_card=agent_card)
    # use client...
```

**After (v1.0):**
```python
from a2a.client import create_client

# From URL — resolves the agent card automatically
client = await create_client('http://localhost:9999/')
async with client:
    # use client...

# From an already-resolved AgentCard
client = await create_client(agent_card)
async with client:
    # use client...
```


> **Example**: [`a2a-mcp-without-framework/client/agent.py` in PR #509](https://github.com/a2aproject/a2a-samples/pull/509/files#diff-56cfce97ff9686166e4b14790ffb7ed46f4c14519261ce5c18365a53cf05e9aa) (`create_client()` usage)

---

## 6. Client: Sending Messages & Handling Responses

### `SendStreamingMessageRequest` removed

There is now a single `send_message()` method on the client that returns a stream of `StreamResponse` proto messages regardless of transport.

**Before (v0.3):**
```python
from a2a.types import (
    Message, MessageSendParams, Part, Role, SendStreamingMessageRequest,
     TextPart,
)
from uuid import uuid4

message_params = MessageSendParams(
    message=Message(
        role=Role.user,
        parts=[Part(TextPart(text=user_input))],
        message_id=uuid4().hex,
        task_id=uuid4().hex,
    )
)
request = SendStreamingMessageRequest(id=uuid4().hex, params=message_params)

response = client.send_message_streaming(request)

```

**After (v1.0):**
```python
from a2a.types import (
    Message, Part, Role, SendMessageRequest,
)
from uuid import uuid4

parts = [Part(text=user_input)]
message = Message(
    role=Role.ROLE_USER,
    parts=parts,
    message_id=uuid4().hex,
)
request = SendMessageRequest(message=message)

async for chunk in client.send_message(request):
    if chunk.HasField('artifact_update'):
        print(get_artifact_text(chunk.artifact_update.artifact))
    elif chunk.HasField('status_update'):
        print(chunk.status_update.status.state)
```

Key differences:
- `send_message_streaming()` → `send_message()` (unified method)
- `SendStreamingMessageRequest` → `SendMessageRequest`
- `MessageSendParams` wrapper is gone; `message` is a field directly on `SendMessageRequest`
- `send_message()` returns `AsyncIterator[StreamResponse]`; iterate with `async for`
- Each `StreamResponse` has a `payload` oneof — use `HasField()` to check which field is set (`'task'`, `'message'`, `'status_update'`, `'artifact_update'`)
- Agent outputs should now be published as **Artifacts**, not status message text

> **Example**: [`helloworld/test_client.py` in PR #474](https://github.com/a2aproject/a2a-samples/pull/474/files#diff-f62c07d3b00364a3100b7effb3e2a1cca0624277d3e40da1bdb07bb46b6a8cef)

---

## 7. Client: Push Notifications Config

`ClientConfig.push_notification_config` is now **singular** (a single `TaskPushNotificationConfig` or `None`), not a list.

**Before (v0.3):**
```python
config = ClientConfig(
    push_notification_configs=[my_push_config],
)
```

**After (v1.0):**
```python
config = ClientConfig(
    push_notification_config=my_push_config,
)
```

---

## 8. Helper Utilities

A new `a2a.helpers` module consolidates helper functions into a single import. Most were previously available under `a2a.utils.*`; a few are new in v1.0.

```python
from a2a.helpers import (
    display_agent_card,             # print a human-readable summary of an AgentCard to stdout
    get_artifact_text,              # join all text parts of an Artifact into a single string (delimiter='\n')
    get_message_text,               # join all text parts of a Message into a single string (delimiter='\n')
    get_stream_response_text,       # extract text from a StreamResponse proto message
    get_text_parts,                 # return a list of raw text strings from a sequence of Parts (skips non-text parts)
    new_artifact,                   # create an Artifact from a list of Parts, name, optional description and artifact_id
    new_message,                    # create a Message from a list of Parts with role (default ROLE_AGENT), optional task_id/context_id
    new_task,                       # create a Task with explicit task_id, context_id, and state
    new_task_from_user_message,     # create a TASK_STATE_SUBMITTED Task from a user Message; raises if role != ROLE_USER or parts are empty
    new_text_artifact,              # create an Artifact with a single text Part, name, optional description and artifact_id
    new_text_artifact_update_event, # create a TaskArtifactUpdateEvent with a text artifact
    new_text_message,               # create a Message with a single text Part; role defaults to ROLE_AGENT
    new_text_status_update_event,   # create a TaskStatusUpdateEvent with a text message
)
```

**Before (v0.3) — reading status message text:**
```python
text = chunk.root.result.status.message.parts[0].root.text
```

**After (v1.0) — reading artifact text:**
```python
from a2a.helpers import get_artifact_text

text = get_artifact_text(chunk.artifact_update.artifact)
```

> In v1.0, agents are expected to publish results as **Artifacts** rather than embedding text in status update messages. Use `TaskArtifactUpdateEvent` (via `event_queue.enqueue_event()`) in your `AgentExecutor` and read from `chunk.artifact_update` on the client side.

---

## 9. Import Path Changes (Quick Reference)

| What | v0.3 import | v1.0 import |
|---|---|---|
| HTTP client for agent | `from a2a.client import A2AClient` | `from a2a.client import create_client` (or `ClientFactory`) |
| Card resolver | `from a2a.client import A2ACardResolver` | `from a2a.client import A2ACardResolver` *(unchanged)* |
| Request handler | `from a2a.server.request_handlers.default_request_handler import DefaultRequestHandler` | `from a2a.server.request_handlers import DefaultRequestHandler` |
| Server setup | `from a2a.server.apps import A2AStarletteApplication` | `from a2a.server.routes import create_jsonrpc_routes, create_agent_card_routes` |
| REST routes | `from a2a.server.apps import A2AStarletteApplication` | `from a2a.server.routes import create_rest_routes` |
| Agent execution | `from a2a.server.agent_execution import AgentExecutor, RequestContext` | *(unchanged)* |
| Task store | `from a2a.server.tasks.inmemory_task_store import InMemoryTaskStore` | *(unchanged)* |
| Types | `from a2a.types import AgentCard, Message, Part, Role, ...` | `from a2a.types import AgentCard, Message, Part, Role, AgentInterface, ...` |
| Helpers | `from a2a.utils.artifact import get_artifact_text` | `from a2a.helpers import get_artifact_text` |
| Message helpers | *(manual construction)* | `from a2a.helpers import new_text_message, new_text_artifact, ...` |
