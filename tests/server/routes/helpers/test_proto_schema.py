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

    struct_descriptor = Default().FindMessageTypeByName(
        'google.protobuf.Struct'
    )
    components = {}
    schema = message_schema(struct_descriptor, components)
    assert schema == {'type': 'object'}
    assert 'Struct' not in components


def test_message_schema_oneof_becomes_allof_with_one_of_constraint():
    components = {}
    message_schema(Part.DESCRIPTOR, components)
    schema = components['Part']
    assert 'allOf' in schema
    one_of_constraint = next(p for p in schema['allOf'] if 'oneOf' in p)
    oneof_keys = {list(v['properties'])[0] for v in one_of_constraint['oneOf']}
    assert {'text', 'raw', 'url', 'data'} <= oneof_keys


def test_message_schema_oneof_variants_have_required():
    components = {}
    message_schema(Part.DESCRIPTOR, components)
    one_of_constraint = next(
        p for p in components['Part']['allOf'] if 'oneOf' in p
    )
    for variant in one_of_constraint['oneOf']:
        assert len(variant['required']) == 1


def test_message_schema_multiple_oneofs_use_allof_not_cartesian_product():
    # Simulate a descriptor with two oneofs: verify allOf has one constraint
    # per oneof rather than a flat list of cross-product variants.
    from unittest.mock import MagicMock

    def _make_field(name):
        f = MagicMock()
        f.name = name
        f.message_type = None
        f.type = 9  # TYPE_STRING
        f.is_repeated = False
        return f

    def _make_oneof(fields):
        o = MagicMock()
        o.fields = fields
        return o

    f_a, f_b = _make_field('a'), _make_field('b')
    f_x, f_y = _make_field('x'), _make_field('y')
    oneof1 = _make_oneof([f_a, f_b])
    oneof2 = _make_oneof([f_x, f_y])

    descriptor = MagicMock()
    descriptor.full_name = 'test.MultiOneof'
    descriptor.name = 'MultiOneof'
    descriptor.oneofs = [oneof1, oneof2]
    descriptor.fields = [f_a, f_b, f_x, f_y]

    components = {}
    message_schema(descriptor, components)
    schema = components['MultiOneof']

    # Should be allOf with two oneOf constraints (one per oneof group),
    # NOT a flat oneOf with 2*2=4 Cartesian-product variants.
    assert 'allOf' in schema
    one_of_constraints = [p for p in schema['allOf'] if 'oneOf' in p]
    assert len(one_of_constraints) == 2
    assert len(one_of_constraints[0]['oneOf']) == 2
    assert len(one_of_constraints[1]['oneOf']) == 2


def test_field_schema_repeated_wraps_in_array():
    components = {}
    msg_descriptor = SendMessageRequest.DESCRIPTOR.fields_by_name[
        'message'
    ].message_type
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
