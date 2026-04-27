"""Proto → JSON Schema helpers shared across transport helpers."""

from typing import Any

from google.protobuf.descriptor import Descriptor, FieldDescriptor
from google.protobuf.message import Message

from a2a.types.a2a_pb2 import SendMessageRequest, TaskPushNotificationConfig


REST_BODY_TYPES: dict[tuple[str, str], type[Message]] = {
    ('/message:send', 'POST'): SendMessageRequest,
    ('/message:stream', 'POST'): SendMessageRequest,
    ('/tasks/{id}/pushNotificationConfigs', 'POST'): TaskPushNotificationConfig,
}

# 64-bit integer types serialize as strings in protojson.
_PROTO_SCALAR_SCHEMAS: dict[int, dict[str, Any]] = {
    FieldDescriptor.TYPE_DOUBLE: {'type': 'number'},
    FieldDescriptor.TYPE_FLOAT: {'type': 'number'},
    FieldDescriptor.TYPE_INT64: {'type': 'string', 'format': 'int64'},
    FieldDescriptor.TYPE_UINT64: {'type': 'string', 'format': 'uint64'},
    FieldDescriptor.TYPE_INT32: {'type': 'integer', 'format': 'int32'},
    FieldDescriptor.TYPE_FIXED64: {'type': 'string', 'format': 'fixed64'},
    FieldDescriptor.TYPE_FIXED32: {'type': 'integer', 'format': 'fixed32'},
    FieldDescriptor.TYPE_BOOL: {'type': 'boolean'},
    FieldDescriptor.TYPE_STRING: {'type': 'string'},
    FieldDescriptor.TYPE_BYTES: {'type': 'string', 'format': 'byte'},
    FieldDescriptor.TYPE_UINT32: {'type': 'integer', 'format': 'uint32'},
    FieldDescriptor.TYPE_SFIXED32: {'type': 'integer'},
    FieldDescriptor.TYPE_SFIXED64: {'type': 'string'},
    FieldDescriptor.TYPE_SINT32: {'type': 'integer'},
    FieldDescriptor.TYPE_SINT64: {'type': 'string'},
}

_WELL_KNOWN_SCHEMAS: dict[str, dict[str, Any]] = {
    'google.protobuf.Timestamp': {'type': 'string', 'format': 'date-time'},
    'google.protobuf.Duration': {'type': 'string'},
    'google.protobuf.Struct': {'type': 'object'},
    'google.protobuf.Value': {},
    'google.protobuf.ListValue': {'type': 'array', 'items': {}},
    'google.protobuf.Empty': {'type': 'object'},
    'google.protobuf.Any': {'type': 'object'},
    'google.protobuf.FieldMask': {'type': 'string'},
}


def field_schema(
    field: FieldDescriptor, components: dict[str, Any]
) -> dict[str, Any]:
    if field.message_type and field.message_type.GetOptions().map_entry:
        value_field = field.message_type.fields_by_name['value']
        return {
            'type': 'object',
            'additionalProperties': field_schema(value_field, components),
        }

    if field.type == FieldDescriptor.TYPE_MESSAGE:
        item = message_schema(field.message_type, components)
    elif field.type == FieldDescriptor.TYPE_ENUM:
        item = {
            'type': 'string',
            'enum': [v.name for v in field.enum_type.values],
        }
    else:
        item = dict(_PROTO_SCALAR_SCHEMAS.get(field.type, {'type': 'string'}))

    if field.is_repeated:
        return {'type': 'array', 'items': item}
    return item


def message_schema(
    descriptor: Descriptor | Any, components: dict[str, Any]
) -> dict[str, Any]:
    """Returns a $ref to descriptor's schema, registering it in components if needed."""
    if descriptor.full_name in _WELL_KNOWN_SCHEMAS:
        return dict(_WELL_KNOWN_SCHEMAS[descriptor.full_name])

    name = descriptor.name
    ref = {'$ref': f'#/components/schemas/{name}'}
    if name in components:
        return ref

    # Reserve the slot before recursing so cyclic types terminate.
    components[name] = {}

    real_oneofs = [o for o in descriptor.oneofs if len(o.fields) > 1]
    oneof_field_names = {f.name for o in real_oneofs for f in o.fields}
    base_properties = {
        f.name: field_schema(f, components)
        for f in descriptor.fields
        if f.name not in oneof_field_names
    }

    if not real_oneofs:
        components[name] = {'type': 'object', 'properties': base_properties}
        return ref

    oneof_constraints = [
        {
            'oneOf': [
                {
                    'type': 'object',
                    'properties': {f.name: field_schema(f, components)},
                    'required': [f.name],
                }
                for f in oneof.fields
            ]
        }
        for oneof in real_oneofs
    ]
    parts: list[dict[str, Any]] = []
    if base_properties:
        parts.append({'type': 'object', 'properties': base_properties})
    parts.extend(oneof_constraints)
    components[name] = parts[0] if len(parts) == 1 else {'allOf': parts}
    return ref
