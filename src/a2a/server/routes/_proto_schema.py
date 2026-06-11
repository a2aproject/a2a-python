"""Proto → JSON Schema utilities for A2A server routes."""

from typing import Any

from google.api import field_behavior_pb2 as fb
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


def _is_required(field: FieldDescriptor) -> bool:
    """Returns True if the field carries google.api.field_behavior = REQUIRED."""
    return fb.REQUIRED in field.GetOptions().Extensions[fb.field_behavior]  # type: ignore[index]  # ty: ignore[invalid-argument-type]


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
        # Well-known types return an inline schema (no $ref); don't wrap them as
        # nullable — they're already inlined as their JSON-Schema equivalent.
        # Repeated fields must not return early here — they fall through to the
        # array-wrapping block below.
        if not field.is_repeated and not _is_required(field) and '$ref' in item:
            return {'oneOf': [item, {'type': 'null'}], 'example': None}
    elif field.type == FieldDescriptor.TYPE_ENUM:
        values = [v.name for v in field.enum_type.values]
        example = next(
            (
                v
                for v in values
                if 'UNSPECIFIED' not in v and 'UNKNOWN' not in v
            ),
            values[0] if values else None,
        )
        item: dict[str, Any] = {'type': 'string', 'enum': values}
        if example:
            item['example'] = example
    else:
        item = dict(_PROTO_SCALAR_SCHEMAS.get(field.type, {'type': 'string'}))
        if field.type == FieldDescriptor.TYPE_STRING:
            # REQUIRED fields must be non-empty; use the field name as a
            # recognisable placeholder. All other strings default to "".
            item['example'] = field.name if _is_required(field) else ''
        elif field.type == FieldDescriptor.TYPE_BOOL:
            item['example'] = False

    if field.is_repeated:
        array_schema: dict[str, Any] = {'type': 'array', 'items': item}
        # Propagate the item example to the array so Swagger pre-fills one entry
        # instead of generating one entry per oneOf branch.
        item_example = (
            components.get(item['$ref'].split('/')[-1], {}).get('example')
            if '$ref' in item
            else item.get('example')
        )
        if item_example is not None:
            array_schema['example'] = [item_example]
        return array_schema
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
    schema: dict[str, Any] = parts[0] if len(parts) == 1 else {'allOf': parts}
    # Provide a single concrete example using the first oneof variant so Swagger
    # doesn't expand every branch into separate array items.
    first_oneof_field = real_oneofs[0].fields[0]
    first_field_schema = field_schema(first_oneof_field, components)
    if 'example' in first_field_schema:
        first_example: Any = first_field_schema['example']
    elif '$ref' in first_field_schema:
        ref_name = first_field_schema['$ref'].split('/')[-1]
        first_example = components.get(ref_name, {}).get('example')
    else:
        _type_defaults: dict[str, Any] = {
            'integer': 0,
            'number': 0.0,
            'boolean': False,
            'array': [],
            'object': {},
        }
        first_example = _type_defaults.get(
            first_field_schema.get('type', 'string'), ''
        )
    schema['example'] = {first_oneof_field.name: first_example}
    components[name] = schema
    return ref
