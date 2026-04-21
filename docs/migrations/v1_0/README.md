# A2A Python SDK Migration Guide: v0.3 → v1.0

The `a2a-sdk` has achieved a major milestone in stability and reliability with the update to full **A2A Protocol v1.0 compatibility**. This guide provides a detailed overview of the breaking changes in version `v1.0` and instructions for migrating your codebase.

Beyond protocol support, `v1.0` enhances the developer experience by introducing unified helper utilities for easier object creation and adopting Starlette route factory functions for more flexible server configuration.

This documentation details the technical upgrades and architectural modifications introduced in A2A Python SDK v1.0. For developers using the database persistence layer, please refer to the [Database Migration Guide](database/) for specific update instructions.

> ### **Why Upgrade to v1.0?**
> * **Protocol v1.0 Compliance**: Full alignment with the latest A2A industry standard for cross-agent interoperability.
> * **Reduced Boilerplate**: Unified helper utilities that simplify common tasks like message and task creation.
> * **Architectural Flexibility**: Direct Starlette/FastAPI integration allows you to mount A2A routes into existing applications with full control over middleware.

---

## Table of Contents

1. [Update Dependencies](#1-update-dependencies)
2. [Types](#2-types)
3. [Server: DefaultRequestHandler](#3-server-defaultrequesthandler)
4. [Server: Application Setup](#4-server-application-setup)
5. [Supporting v0.3 Clients](#5-supporting-v03-clients)
6. [Client: Creating a Client](#6-client-creating-a-client)
7. [Client: Send Message](#7-client-send-message)
8. [Client: Push Notifications Config](#8-client-push-notifications-config)
9. [Helper Utilities](#9-helper-utilities)
10. [Summary of Key Changes](#10-summary-of-key-changes-in-v10)
11. [Get Started](#11-get-started)

---

## 1. Update Dependencies

(UV users) To upgrade to the latest version of the `a2a-sdk`, update the dependencies section in your `pyproject.toml` file.

| File             | Before (`v0.3`)                   | After (`v1.0`)                    |
|------------------|-----------------------------------|-----------------------------------|
| `pyproject.toml` | dependencies = ["a2a-sdk>=0.3.0"] | dependencies = ["a2a-sdk>=1.0.0"] |

**Installation**

After updating your configuration file, sync your environment:

* Using UV:

```bash
uv sync
```

* Using pip:

```bash
pip install --upgrade a2a-sdk
```

---

## 2. Types

[Types](https://github.com/a2aproject/a2a-python/blob/main/src/a2a/types/a2a_pb2.pyi) have migrated from Pydantic models to Protobuf-based classes.


### Enum values: `snake_case` → `SCREAMING_SNAKE_CASE`

All the enum values are now [standardized](https://a2a-protocol.org/v1.0.0/specification/#55-json-field-naming-convention) to use `SCREAMING_SNAKE_CASE` format.

This affects every enum in the SDK: `TaskState`, `Role`.

| Enum | v0.3 | v1.0 |
|---|---|---|
| `TaskState` | `TaskState.submitted` | `TaskState.TASK_STATE_SUBMITTED` |
| `TaskState` | `TaskState.working` | `TaskState.TASK_STATE_WORKING` |
| `TaskState` | `TaskState.completed` | `TaskState.TASK_STATE_COMPLETED` |
| `TaskState` | `TaskState.failed` | `TaskState.TASK_STATE_FAILED` |
| `TaskState` | `TaskState.canceled` | `TaskState.TASK_STATE_CANCELED` |
| `TaskState` | `TaskState.input_required` | `TaskState.TASK_STATE_INPUT_REQUIRED` |
| `TaskState` | `TaskState.auth_required` | `TaskState.TASK_STATE_AUTH_REQUIRED` |
| `TaskState` | `TaskState.rejected` | `TaskState.TASK_STATE_REJECTED` |
| `TaskState` | | 🆕 `TaskState.TASK_STATE_UNSPECIFIED` |
|||
| `Role` | `Role.user` | `Role.ROLE_USER` |
| `Role` | `Role.agent` | `Role.ROLE_AGENT` |
| `Role` | | 🆕 `Role.ROLE_UNSPECIFIED` |

> **Example**: [`a2a-mcp-without-framework/server/agent_executor.py` in PR #509](https://github.com/a2aproject/a2a-samples/pull/509/changes#diff-1f9b098f9f82ee40666ee61db56dc2246281423c445bcf017079c53a0a05954f)

### Message and Part construction

Constructing messages is simplified in v1.0. The old API required wrapping content in an intermediate type (`TextPart`, `FilePart`, `DataPart`) before placing it inside a `Part`. In v1.0, `Part` is a single unified message — set the content type directly on it and the wrapper types are gone entirely.

Key changes:
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

Using [A2A helper utilities](#9-helper-utilities)

```python
from a2a.helpers import new_text_message
from a2a.types import Role

# Use the helper function to create `Hello` message
message = new_text_message(text="Hello", role=Role.ROLE_USER)
```

Without helper utils, you can still construct directly

```python
from a2a.types import Message, Part, Role
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

Key changes:
- Added `AgentInterface` class to support multiple transport bindings via the newly added `supported_interfaces` field in AgentCard.
- The `url` parameter in `AgentCard` is removed and is now part of `AgentInterface`.
- Accepted values for `AgentInterface.protocol_binding`: `'JSONRPC'`, `'HTTP+JSON'`, `'GRPC'`
- The `AgentCard.capabilities` field is renamed to `AgentCard.agent_capabilities`.
- The `AgentCard.supports_authenticated_extended_card` field is renamed to `AgentCapabilities.extended_agent_card`.
- The `AgentCapabilities.input_modes` and `AgentCapabilities.output_modes` fields are removed; use `AgentCard.default_input_modes` and `AgentCard.default_output_modes` for card-level defaults, or `AgentSkill.input_modes` and `AgentSkill.output_modes` for per-skill overrides.
- The `examples` parameter in `AgentCard` is removed and is now part of `AgentSkill`.

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
        # JSON-RPC
        AgentInterface(
            protocol_binding='JSONRPC',
            url='http://localhost:41241/a2a/jsonrpc/',
        ),
        # GRPC
        AgentInterface(
            protocol_binding='GRPC',
            url='http://localhost:50051/a2a/grpc/',
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

The application wrapper classes (`A2AStarletteApplication`, `A2AFastApiApplication` and `A2ARESTFastApiApplication`) are now removed. The Server setup now uses Starlette route factory functions directly, giving you better control over the routing, middleware, authentication, logging and other aspects of the server.

**Before (v0.3):**
```python
from a2a.server.apps import A2AStarletteApplication
import uvicorn

# Create application using A2AStarletteApplication wrapper class
server = A2AStarletteApplication(
    agent_card=agent_card,
    http_handler=request_handler,
)

# Start the server
uvicorn.run(server.build(), host=host, port=port)
```

**After (v1.0):**

Define routes for each supported transport as per AgentCard.

```python
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes

# Define routes for transports as per AgentCard
routes = []
# A2A Agent Card routes
routes.extend(create_agent_card_routes(agent_card))
# JSON-RPC routes
routes.extend(create_jsonrpc_routes(request_handler, rpc_url='/api/v1/jsonrpc/'))

# Optional: Add routes for REST/HTTP transports
# routes.extend(create_rest_routes(request_handler, path_prefix='/api/v1/rest/'))
```

Add the routes to the application:

```python
from starlette.applications import Starlette
import uvicorn

# Create application using routes
app = Starlette(routes=routes)

# Start the server
uvicorn.run(app, host=host, port=port)
```

If you prefer FastAPI for your server application:

```python
from fastapi import FastAPI
import uvicorn

# Create application using routes
app = FastAPI(routes=routes)

# Start the server
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

In `v1.0`, use `a2a.client.create_client()` helper function to create a `Client` for the agent.


**Before (v0.3):**
```python
from a2a.client import ClientFactory

# Option 1: Using Agent Server URL
factory = ClientFactory()
client = factory.create_client('http://localhost:9999/')

# Option 2: Using AgentCard
factory = ClientFactory()
client = factory.create_client(agent_card)
```

**After (v1.0):**
```python
from a2a.client import create_client

# Option 1: Using Agent Server URL
client = await create_client('http://localhost:9999/')

# Option 2: Using AgentCard
client = await create_client(agent_card)
```


> **Example**: [`a2a-mcp-without-framework/client/agent.py` in PR #509](https://github.com/a2aproject/a2a-samples/pull/509/files#diff-56cfce97ff9686166e4b14790ffb7ed46f4c14519261ce5c18365a53cf05e9aa) (`create_client()` usage)

---

## 7. Client: Send Message

The `BaseClient.send_message()` return type is standardized from `AsyncIterator[ClientEvent | Message]` to `AsyncIterator[StreamResponse]`.

Each `StreamResponse` yields exactly one of: (`task`, `message`, `status_update`, or `artifact_update`). Use `HasField()` to check which field is set.


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

To improve the developer experience, we have consolidated helper functions into a single import. In v0.3, these helper functions were scattered across different modules. In v1.0, they are all available under `a2a.helpers`.

| Helper Function | Description |
|---|---|
| `display_agent_card` | Prints a human-readable summary of an `AgentCard` to stdout. |
| `get_artifact_text` | Joins all text parts of an `Artifact` into a single string (using `\n` as delimiter). |
| `get_message_text` | Joins all text parts of a `Message` into a single string (using `\n` as delimiter). |
| `get_stream_response_text` | Extracts text from a `StreamResponse` protobuf message. |
| `get_text_parts` | Returns a list of raw text strings from a sequence of `Part` objects, skipping non-text parts. |
| `new_artifact` | Creates an `Artifact` from a list of `Part` objects, a name, and an optional description and ID. |
| `new_message` | Creates a `Message` from a list of `Part` objects with a role (defaults to `ROLE_AGENT`), and optional task/context IDs. |
| `new_task` | Creates a `Task` with an explicit task ID, context ID, and state. |
| `new_task_from_user_message` | Creates a `TASK_STATE_SUBMITTED` `Task` from a user `Message`. Raises an error if the role is not `ROLE_USER` or if parts are empty. |
| `new_text_artifact` | Creates an `Artifact` with a single text `Part`, a name, and an optional description and ID. |
| `new_text_artifact_update_event` | Creates a `TaskArtifactUpdateEvent` with a text artifact. |
| `new_text_message` | Creates a `Message` with a single text `Part`; role defaults to `ROLE_AGENT`. |
| `new_text_status_update_event` | Creates a `TaskStatusUpdateEvent` with a text message. |

Example Usage: 

**1. Create text based message**

```python
from a2a.helpers import new_text_message
from a2a.types import Role

# Create a user message
user_message = new_text_message("What's the weather?", role=Role.ROLE_USER)

# Create an agent response message
response_message = new_text_message("It is sunny today!")
```

**2. Extract the text out of a message**

```python
from a2a.helpers import get_message_text

# Get text from a message
text = get_message_text(response_message)
print(text)
```

---

## 10. Summary of Key Changes in v1.0

- **Migration to Protobuf** — Core types have migrated from Pydantic models to Protobuf-based classes. Protobuf objects do not support arbitrary attribute assignment. Use `MessageToDict` from `google.protobuf.json_format` to convert objects to dictionaries, and `HasField('field_name')` to check for optional fields.
- **Standardization to `SCREAMING_SNAKE_CASE`** — All enum values have been renamed from `snake_case` strings to `SCREAMING_SNAKE_CASE` for compliance with the ProtoJSON specification.
- **`AgentCard`** — Significantly restructured to support multiple transport interfaces.
  - **`AgentInterface`** — The top-level `url` field is replaced by `supported_interfaces`, a list of `AgentInterface` objects. Each entry describes a single transport endpoint carrying `protocol_binding`, `protocol_version`, and `url`.
  - **Input and output modes** — `AgentCapabilities.input_modes` and `AgentCapabilities.output_modes` are removed and now live directly on `AgentCard` as `default_input_modes` and `default_output_modes`. Individual skills can override these with their own `input_modes` and `output_modes`.
- **Application setup** — The wrapper classes (`A2AStarletteApplication`, `A2AFastApiApplication` and `A2ARESTFastApiApplication`) are now removed. Server setup now uses route factory functions `create_jsonrpc_routes()`, `create_rest_routes()`, `create_agent_card_routes()` composed directly into a Starlette or FastAPI app.
- **Helper utilities** — A new `a2a.helpers` module consolidates all helper functions under a single import, replacing the scattered `a2a.utils.*` modules and adding new helpers for constructing and reading v1.0 proto types.

---

## 11. Get Started

The fastest way to see v1.0 in action is to run the samples:

| File | Role | Description |
|---|---|---|
| [`samples/hello_world_agent.py`](../../../samples/hello_world_agent.py) | **Server** | A2A agent exposing JSON-RPC, REST, and gRPC — with v0.3 compat enabled |
| [`samples/cli.py`](../../../samples/cli.py) | **Client** | Interactive terminal client; supports all three transports |

```bash
# In one terminal — start the agent:
uv run python samples/hello_world_agent.py

# In another — connect with the CLI:
uv run python samples/cli.py
```

Then type a message like `hello` and press Enter. See [`samples/README.md`](../../../samples/README.md) for full details.

For more examples see the [a2a-samples repository](https://github.com/a2aproject/a2a-samples/tree/main/samples/python).
