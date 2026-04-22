# A2A Protocol Backward Compatibility (v0.3)

This directory (`src/a2a/compat/v0_3/`) provides the foundational types and translation layers necessary for modern `v1.0` clients and servers to interoperate with legacy `v0.3` A2A systems.

## Data Representations

To support cross-version compatibility across JSON, REST, and gRPC, this directory manages three distinct data representations:

### 1. Legacy v0.3 Pydantic Models (`types.py`)
This file contains Python [Pydantic](https://docs.pydantic.dev/) models generated from the legacy v0.3 JSON schema. 
* **Purpose**: This is the "pivot" format. Legacy JSON-RPC and REST implementations natively serialize to/from these models. It acts as the intermediary between old wire formats and the modern SDK.

### 2. Legacy v0.3 Protobuf Bindings (`a2a_v0_3_pb2.py`)
This module contains the native Protobuf bindings for the legacy v0.3 gRPC protocol.
* **Purpose**: To decode incoming bytes from legacy gRPC clients or encode outbound bytes to legacy gRPC servers. 
* **Note**: It is generated into the `a2a.v1` package namespace.

### 3. Current v1.0 Protobuf Bindings (`a2a.types.a2a_pb2`)
This is the central source of truth for the modern SDK (`v1.0`). All legacy payloads must ultimately be translated into these `v1.0` core objects to be processed by the modern `AgentExecutor`.
* **Note**: It is generated into the `lf.a2a.v1` package namespace.
---

## Transformation Utilities

Payloads arriving from legacy clients undergo a phased transformation to bridge the gap between versions.

### Legacy gRPC ↔ Legacy Pydantic: `proto_utils.py`
This module handles the mapping between legacy `v0.3` gRPC Protobuf objects and legacy `v0.3` Pydantic models.
This is a copy of the `a2a.types.proto_utils` module from 0.3 release.

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

### Legacy Pydantic ↔ Modern v1.0 Protobuf: `conversions.py`
This module structurally translates between legacy `v0.3` Pydantic objects and modern `v1.0` Core Protobufs.

```python
from a2a.types import a2a_pb2 as pb2_v10
from a2a.compat.v0_3 import conversions

# 3. Convert the legacy Pydantic object into a modern v1.0 Protobuf
core_pb_msg: pb2_v10.Message = conversions.to_core_message(pydantic_msg)

```
