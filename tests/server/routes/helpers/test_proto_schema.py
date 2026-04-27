from a2a.server.routes.helpers._proto_schema import (
    REST_BODY_TYPES,
    field_schema,
    message_schema,
)
from a2a.types.a2a_pb2 import Message, Part, SendMessageRequest


def test_message_schema_registers_ref():
    components = {}
    ref = message_schema(SendMessageRequest.DESCRIPTOR, components)
    assert ref == {'$ref': '#/components/schemas/SendMessageRequest'}
    assert 'SendMessageRequest' in components


def test_message_schema_returns_cached_ref():
    components = {}
    ref1 = message_schema(SendMessageRequest.DESCRIPTOR, components)
    ref2 = message_schema(SendMessageRequest.DESCRIPTOR, components)
    assert ref1 == ref2


def test_message_schema_recurses_into_nested_types():
    components = {}
    message_schema(SendMessageRequest.DESCRIPTOR, components)
    assert 'Message' in components
    assert 'Part' in components


def test_message_schema_well_known_type_inline():
    from google.protobuf.descriptor_pool import Default
    struct_descriptor = Default().FindMessageTypeByName('google.protobuf.Struct')
    components = {}
    schema = message_schema(struct_descriptor, components)
    assert schema == {'type': 'object'}
    assert 'Struct' not in components


def test_message_schema_oneof_becomes_one_of():
    components = {}
    message_schema(Part.DESCRIPTOR, components)
    schema = components['Part']
    assert 'oneOf' in schema
    oneof_keys = {list(v['properties'])[-1] for v in schema['oneOf']}
    assert {'text', 'raw', 'url', 'data'} <= oneof_keys


def test_message_schema_oneof_variants_have_required():
    components = {}
    message_schema(Part.DESCRIPTOR, components)
    for variant in components['Part']['oneOf']:
        assert len(variant['required']) == 1


def test_field_schema_repeated_wraps_in_array():
    components = {}
    msg_descriptor = SendMessageRequest.DESCRIPTOR.fields_by_name['message'].message_type
    parts_field = msg_descriptor.fields_by_name['parts']
    schema = field_schema(parts_field, components)
    assert schema['type'] == 'array'
    assert 'items' in schema


def test_field_schema_enum():
    role_field = Message.DESCRIPTOR.fields_by_name['role']
    schema = field_schema(role_field, {})
    assert schema['type'] == 'string'
    assert 'ROLE_USER' in schema['enum']
    assert 'ROLE_AGENT' in schema['enum']


def test_field_schema_map_entry():
    metadata_field = SendMessageRequest.DESCRIPTOR.fields_by_name['metadata']
    schema = field_schema(metadata_field, {})
    assert schema == {'type': 'object'}


def test_rest_body_types_coverage():
    assert ('/message:send', 'POST') in REST_BODY_TYPES
    assert ('/message:stream', 'POST') in REST_BODY_TYPES
    assert ('/tasks/{id}/pushNotificationConfigs', 'POST') in REST_BODY_TYPES
