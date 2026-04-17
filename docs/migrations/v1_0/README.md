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


### Enum values: kebab-case → SCREAMING_SNAKE_CASE

All enum values have been renamed from kebab-case strings to `SCREAMING_SNAKE_CASE`.

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

### AgentCard Structure

The `AgentCard` has been significantly restructured to support multiple transport interfaces.

#### `url` → `supported_interfaces`

The top-level `url` field is replaced by a list of `AgentInterface` objects, each describing a specific transport endpoint.

**Before (v0.3):**
```python
from a2a.types import AgentCard, AgentCapabilities, AgentSkill

agent_card = AgentCard(
    name='My Agent',
    description='...',
    url='http://localhost:9999/',
    version='1.0.0',
    default_input_modes=['text'],
    default_output_modes=['text'],
    supports_authenticated_extended_card=True,
    capabilities=AgentCapabilities(
        input_modes=['text'],
        output_modes=['text'],
        streaming=True,
    ),
    skills=[skill],
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
    default_input_modes=['text'],
    default_output_modes=['text'],
    capabilities=AgentCapabilities(
        streaming=True,
        extended_agent_card=True,
    ),
    skills=[skill],
)
```

Key differences:
- `url` is gone; use `supported_interfaces` with one or more `AgentInterface` entries
- `AgentCapabilities.input_modes` and `AgentCapabilities.output_modes` are removed
- `supports_authenticated_extended_card` is no longer a top-level `AgentCard` field; it has moved into `AgentCapabilities` and is renamed to `extended_agent_card`
- `AgentInterface.protocol_binding` accepted values: `'JSONRPC'`, `'HTTP_JSON'`, `'GRPC'`

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

---

## 5. Client: Creating a Client

The `A2AClient` class has been removed. Use the new `create_client()` factory function or `ClientFactory`.

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
async with await create_client('http://localhost:9999/') as client:
    # use client...

# From an already-resolved AgentCard
async with await create_client(agent_card) as client:
    # use client...
```

### Advanced usage: `ClientFactory`

For reusing connections across multiple agents, registering custom transports, or configuring timeouts:

```python
from a2a.client import ClientFactory, ClientConfig

config = ClientConfig(streaming=True)
factory = ClientFactory(config)

# Create from URL (async)
client = await factory.create_from_url('http://localhost:9999/')

# Create from AgentCard (sync)
client = factory.create(agent_card)
```

### `ClientTaskManager` and `Consumers` removed

The `ClientTaskManager` class and `Consumers` abstraction have been removed. Response handling is now done directly by iterating the stream returned from `send_message()`.

---

## 6. Client: Sending Messages & Handling Responses

### `SendStreamingMessageRequest` removed

There is now a single `send_message()` method on the client that returns a stream of `StreamResponse` proto messages regardless of transport.

**Before (v0.3):**
```python
from a2a.types import (
    Message, MessageSendParams, Part, Role, SendStreamingMessageRequest,
    SendStreamingMessageSuccessResponse, TaskStatusUpdateEvent, TextPart,
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

async for chunk in client.send_message_streaming(request):
    if isinstance(chunk.root, SendStreamingMessageSuccessResponse) and \
       isinstance(chunk.root.result, TaskStatusUpdateEvent):
        msg = chunk.root.result.status.message
        if msg:
            print(msg.parts[0].root.text)
```

**After (v1.0):**
```python
from a2a.helpers import get_artifact_text, new_text_message
from a2a.types import SendMessageRequest

message = new_text_message(text=user_input)
request = SendMessageRequest(message=message)

async for chunk in client.send_message(request):
    if chunk.HasField('artifact_update'):
        text = get_artifact_text(chunk.artifact_update.artifact)
        if text:
            print(text)
    elif chunk.HasField('status_update'):
        # handle status updates
        ...
```

Key differences:
- `send_message_streaming()` → `send_message()` (unified method)
- `SendStreamingMessageRequest` → `SendMessageRequest`
- `MessageSendParams` wrapper is gone; `message` is a field directly on `SendMessageRequest`
- Response chunks are `StreamResponse` proto messages; use `HasField()` to check the payload type
- Agent outputs should now be published as **Artifacts**, not status message text

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

A new `a2a.helpers` module provides convenience functions previously scattered across `a2a.utils.*` and adds new helpers for v1.0 proto types.

```python
from a2a.helpers import (
    # --- moved from a2a.utils.* ---
    new_text_message,               # was a2a.utils.message.new_agent_text_message; gained role param
    new_message,                    # was a2a.utils.message.new_agent_parts_message; gained role param
    get_message_text,               # was a2a.utils.message.get_message_text
    new_text_artifact,              # was a2a.utils.artifact.new_text_artifact; gained artifact_id param
    new_artifact,                   # was a2a.utils.artifact.new_artifact; gained artifact_id param
    get_artifact_text,              # was a2a.utils.artifact.get_artifact_text
    get_text_parts,                 # was a2a.utils.parts.get_text_parts
    new_task_from_user_message,     # was a2a.utils.task.new_task; renamed, now validates role == ROLE_USER

    # --- new in v1.0 ---
    new_task,                       # create a Task with explicit task_id, context_id, and state
    new_text_artifact_update_event, # create a TaskArtifactUpdateEvent with a text artifact
    new_text_status_update_event,   # create a TaskStatusUpdateEvent with a text message
    get_stream_response_text,       # extract text from a StreamResponse proto message
    display_agent_card,             # print a human-readable summary of an AgentCard to stdout
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
