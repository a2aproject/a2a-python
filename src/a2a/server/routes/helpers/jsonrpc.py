"""JSON-RPC specific helpers for A2A server routes."""

from typing import Any

from google.protobuf.message import Message

from a2a.server.routes.helpers._proto_schema import message_schema
from a2a.types.a2a_pb2 import (
    CancelTaskRequest,
    DeleteTaskPushNotificationConfigRequest,
    GetExtendedAgentCardRequest,
    GetTaskPushNotificationConfigRequest,
    GetTaskRequest,
    ListTaskPushNotificationConfigsRequest,
    ListTasksRequest,
    SendMessageRequest,
    SubscribeToTaskRequest,
    TaskPushNotificationConfig,
)


METHOD_TYPES: dict[str, type[Message]] = {
    'SendMessage': SendMessageRequest,
    'SendStreamingMessage': SendMessageRequest,
    'GetTask': GetTaskRequest,
    'ListTasks': ListTasksRequest,
    'CancelTask': CancelTaskRequest,
    'CreateTaskPushNotificationConfig': TaskPushNotificationConfig,
    'GetTaskPushNotificationConfig': GetTaskPushNotificationConfigRequest,
    'ListTaskPushNotificationConfigs': ListTaskPushNotificationConfigsRequest,
    'DeleteTaskPushNotificationConfig': DeleteTaskPushNotificationConfigRequest,
    'SubscribeToTask': SubscribeToTaskRequest,
    'GetExtendedAgentCard': GetExtendedAgentCardRequest,
}

DESCRIPTION = """\
A2A JSON-RPC 2.0 endpoint. The `method` field selects the operation;
`params` must match that method's schema (see the `oneOf` below).

**Supported methods:**

- `SendMessage` — Send a message to the agent (returns a Task or response Message).
- `SendStreamingMessage` — Send a message and receive a Server-Sent Events stream.
- `GetTask` — Fetch a task by ID.
- `ListTasks` — List tasks with pagination and filtering.
- `CancelTask` — Cancel an in-progress task.
- `CreateTaskPushNotificationConfig` — Register a push-notification config on a task.
- `GetTaskPushNotificationConfig` — Read a single push-notification config.
- `ListTaskPushNotificationConfigs` — List all push-notification configs for a task.
- `DeleteTaskPushNotificationConfig` — Delete a push-notification config.
- `SubscribeToTask` — Subscribe to task events via Server-Sent Events.
- `GetExtendedAgentCard` — Fetch the authenticated extended agent card.
"""


def envelope_schema(components: dict[str, Any]) -> dict[str, Any]:
    """Builds the A2ARequest JSON-RPC envelope schema with a oneOf over all method params."""
    seen_refs: set[str] = set()
    params_refs: list[dict[str, Any]] = []
    for cls in METHOD_TYPES.values():
        ref = message_schema(cls.DESCRIPTOR, components)
        key = ref.get('$ref', '')
        if key and key not in seen_refs:
            seen_refs.add(key)
            params_refs.append(ref)

    components['A2ARequest'] = {
        'type': 'object',
        'required': ['jsonrpc', 'method'],
        'properties': {
            'jsonrpc': {'type': 'string', 'enum': ['2.0']},
            'id': {
                'oneOf': [
                    {'type': 'string'},
                    {'type': 'integer'},
                    {'type': 'null'},
                ],
            },
            'method': {
                'type': 'string',
                'enum': list(METHOD_TYPES),
            },
            'params': {'oneOf': params_refs},
        },
    }
    return {'$ref': '#/components/schemas/A2ARequest'}
