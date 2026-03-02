# A2A Protocol Backward Compatibility (v0.3)

This directory (`src/a2a/compat/v0_3/`) isolates the legacy `v0.3` A2A protocol implementation, providing the translation layers necessary for modern `v1.0` clients and servers to seamlessly interoperate with older systems.

## Data Representations

To maintain perfect compatibility across differing JSON, REST, and gRPC topologies, this directory manages three distinct data representations:

### 1. Legacy v0.3 Pydantic Models (`types.py`)
This file contains the foundational Python [Pydantic](https://docs.pydantic.dev/) models generated from the legacy v0.3 JSON schema. 
* **Purpose**: This is the universal "pivot" format. Legacy JSON-RPC and REST adapters natively serialize to/from these models. It acts as the intermediary stepping stone between the old wire formats and the new SDK.

### 2. Legacy v0.3 Protobuf Bindings (`a2a_v0_3_pb2.py`)
This dynamically generated module contains the native Protobuf bindings for the legacy v0.3 gRPC protocol.
* **Purpose**: To decode incoming bytes from legacy gRPC clients or encode outbound bytes to legacy gRPC servers. 
* **Note**: It is explicitly generated into the `a2a.compat.v0_3` package namespace to safely avoid global `DescriptorPool` collisions with the modern `v1.0` bindings.

### 3. Current v1.0 Protobuf Bindings (`a2a.types.a2a_pb2`)
This is the central source of truth for the entire modern SDK (`v1.0`).
* **Purpose**: Regardless of what legacy protocol or transport a payload arrived on, it must ultimately be translated into these `v1.0` core objects before being passed into the application's `AgentExecutor`.

---

## Transformation Utilities

Because the modern SDK engine expects `v1.0` Protobuf objects, payloads arriving from `v0.3` clients undergo a phased transformation.

### Phase 1: `proto_utils.py` (Legacy gRPC ↔ Legacy Pydantic)
This module exposes two static classes, `ToProto` and `FromProto`. It handles the recursive mapping between the legacy `v0.3` gRPC Protobuf objects and the legacy `v0.3` Pydantic models.

```python
from a2a.compat.v0_3 import a2a_v0_3_pb2
from a2a.compat.v0_3 import types as types_v03
from a2a.compat.v0_3 import proto_utils

# 1. Receive legacy bytes over the wire
legacy_pb_msg = a2a_v0_3_pb2.Message()
legacy_pb_msg.ParseFromString(wire_bytes)

# 2. Convert to intermediate Pydantic representation
pydantic_msg: types_v03.Message = proto_utils.FromProto.message(legacy_pb_msg)
```

### Phase 2: `conversions.py` (Legacy Pydantic ↔ Modern v1.0 Protobuf)
This module bridges the final gap. It contains standalone mapping functions prefixed with `to_core_*` and `to_compat_*` to structurally translate between the legacy `v0.3` Pydantic objects and the modern `v1.0` Core Protobufs.

```python
from a2a.types import a2a_pb2 as pb2_v10
from a2a.compat.v0_3 import conversions

# 3. Convert the legacy Pydantic object into a modern v1.0 Protobuf
core_pb_msg: pb2_v10.Message = conversions.to_core_message(pydantic_msg)

# The SDK's AgentExecutor now consumes `core_pb_msg`.
```

---

## Complete End-to-End Workflow

When a modern `v1.0` Server receives a legacy `v0.3` gRPC request, the data flows seamlessly through the stack:

1. **Wire Decode**: `grpc_adapter.py` catches the raw bytes and parses them natively into `a2a.compat.v0_3.a2a_v0_3_pb2.SendMessageRequest`.
2. **First Translation**: `proto_utils.FromProto.message_send_params()` converts the legacy protobuf object into the intermediate `types.MessageSendParams` Pydantic model.
3. **Second Translation**: `conversions.to_core_send_message_request()` converts the Pydantic object into the final, modern `a2a.types.a2a_pb2.SendMessageRequest`.
4. **Execution**: The server executes the `AgentExecutor` using the modern request.
5. **Response Translation**: The generated `v1.0` response (`a2a.types.a2a_pb2.SendMessageResponse`) reverses the chain: it is mapped to a `v0.3` Pydantic model via `conversions.to_compat_send_message_response()`, transformed into a `v0.3` Protobuf via `proto_utils.ToProto.task_or_message()`, and serialized to bytes for the legacy client.