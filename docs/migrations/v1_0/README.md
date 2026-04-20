# Migration Guide: v0.3 → v1.0

This guide covers the breaking changes introduced in `a2a-sdk` v1.0 and explains how to update your code. The changes reflect updates to the A2A protocol specification: [What's new in v1.0](https://a2a-protocol.org/latest/whats-new-v1/).

> **Related guides**: If you use the database persistence layer, also see the [Database Migration Guide](database/).

---

## Table of Contents

1. [Package Dependency](#1-package-dependency)
2. [Types](#2-types)
3. [Server: DefaultRequestHandler](#3-server-defaultrequesthandler)
4. [Server: Application Setup](#4-server-application-setup)
5. [Supporting v0.3 Clients](#5-supporting-v03-clients)
6. [Client: Creating a Client](#6-client-creating-a-client)
7. [Client: Send Message](#7-client-send-message)
8. [Client: Push Notifications Config](#8-client-push-notifications-config)
9. [Helper Utilities](#9-helper-utilities)

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

Key differences:
- `Part(TextPart(text=...))` → `Part(text=...)` (flat union field)
- `Role.user` → `Role.ROLE_USER`, `Role.agent` → `Role.ROLE_AGENT`

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

> **Example**: [`helloworld/test_client.py` in PR #474](https://github.com/a2aproject/a2a-samples/pull/474/files#diff-f62c07d3b00364a3100b7effb3e2a1cca0624277d3e40da1bdb07bb46b6a8cef)

### AgentCard Structure

The `AgentCard` has been significantly restructured to support multiple transport interfaces.

Key differences:
- `url` is gone; use `supported_interfaces` with one or more `AgentInterface` entries
- `AgentCapabilities.input_modes` and `AgentCapabilities.output_modes` are removed; use `AgentCard.default_input_modes` / `AgentCard.default_output_modes` for card-level defaults, or `AgentSkill.input_modes` / `AgentSkill.output_modes` for per-skill overrides
- `supports_authenticated_extended_card` is no longer a top-level `AgentCard` field; it has moved into `AgentCapabilities` and is renamed to `extended_agent_card`
- `AgentInterface.protocol_binding` accepted values: `'JSONRPC'`, `'HTTP+JSON'`, `'GRPC'`
- `examples` field was removed; set it per `AgentSkill` instead

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

The `A2AStarletteApplication`, `A2AFastApiApplication` and `A2ARESTFastApiApplication` wrapper classes have been removed. Server setup now uses **Starlette route factory functions** directly, giving you full control over the routing.

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

## 5. Supporting v0.3 Clients

If you cannot update all clients at once, you can run a v1.0 server that simultaneously accepts v0.3 connections. Two changes are needed.

**1. Add the v0.3 AgentInterface to `supported_interfaces` in your `AgentCard`**:

```python
supported_interfaces=[
    AgentInterface(protocol_binding='JSONRPC', protocol_version='0.3', url='http://localhost:9999/'),
]
```

**2. Enable the compat flag** on the relevant route factory:

```python
create_jsonrpc_routes(request_handler, rpc_url='/', enable_v0_3_compat=True)
create_rest_routes(request_handler, enable_v0_3_compat=True)
```

> For a full working example see [`samples/hello_world_agent.py`](../../../samples/hello_world_agent.py). For known limitations see [issue #742](https://github.com/a2aproject/a2a-python/issues/742).

---

## 6. Client: Creating a Client

New `create_client()` `ClientFactory` function that creates a client for the agent.

> **Note**: The legacy `A2AClient` class has been removed.

**Before (v0.3):**
```python
from a2a.client import ClientFactory

# From URL
factory = ClientFactory()
client = factory.create_client('http://localhost:9999/')

# From an already-resolved AgentCard
factory = ClientFactory()
client = factory.create_client(agent_card)
```

**After (v1.0):**
```python
from a2a.client import create_client

# From URL — resolves the agent card automatically
client = await create_client('http://localhost:9999/')

# From an already-resolved AgentCard
client = await create_client(agent_card)
```


> **Example**: [`a2a-mcp-without-framework/client/agent.py` in PR #509](https://github.com/a2aproject/a2a-samples/pull/509/files#diff-56cfce97ff9686166e4b14790ffb7ed46f4c14519261ce5c18365a53cf05e9aa) (`create_client()` usage)

---

## 7. Client: Send Message

The `BaseClient.send_message()` return type is standardised from `AsyncIterator[ClientEvent | Message]` to  `AsyncIterator[StreamResponse]`.

Each `StreamResponse` yields exactly one of: `task`, `message`, `status_update`, or `artifact_update`. Use `HasField()` to check which field is set.


**Before (v0.3):**
```python
async for event, message in client.send_message(request):
    if isinstance(event, Task):
        ...
    if isinstance(event, UpdateEvent):
        ...
    if message:
        ...
```

**After (v1.0):**
```python
async for chunk in client.send_message(request):
    if chunk.HasField('artifact_update'):
        ...
    elif chunk.HasField('status_update'):
        ...
    elif chunk.HasField('task'):
        ...
    elif chunk.HasField('message'):
        ...
```


---

## 8. Client: Push Notifications Config

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

## 9. Helper Utilities

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

