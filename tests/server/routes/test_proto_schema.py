from a2a.server.routes._proto_schema import (
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


def test_field_schema_enum_example_skips_unspecified():
    role_field = Message.DESCRIPTOR.fields_by_name['role']
    schema = field_schema(role_field, {})
    assert schema['example'] == 'ROLE_USER'


def test_field_schema_string_example_is_empty():
    context_id_field = Message.DESCRIPTOR.fields_by_name['context_id']
    schema = field_schema(context_id_field, {})
    assert schema['example'] == ''


def test_field_schema_string_required_uses_field_name():
    # REQUIRED string fields must be non-empty; the field name is the placeholder.
    message_id_field = Message.DESCRIPTOR.fields_by_name['message_id']
    schema = field_schema(message_id_field, {})
    assert schema['example'] == 'message_id'


def test_field_schema_bool_example_is_false():
    from a2a.types.a2a_pb2 import SendMessageConfiguration

    field = SendMessageConfiguration.DESCRIPTOR.fields_by_name[
        'return_immediately'
    ]
    schema = field_schema(field, {})
    assert schema['example'] is False


def test_field_schema_optional_message_is_nullable():
    # Non-REQUIRED message fields default to null so Swagger doesn't pre-fill them
    # with empty sub-fields that trigger server-side required-field validation.
    from a2a.types.a2a_pb2 import SendMessageConfiguration

    field = SendMessageConfiguration.DESCRIPTOR.fields_by_name[
        'task_push_notification_config'
    ]
    schema = field_schema(field, {})
    assert schema['example'] is None
    assert any(v == {'type': 'null'} for v in schema['oneOf'])


def test_field_schema_required_message_is_not_nullable():
    from a2a.types.a2a_pb2 import SendMessageRequest

    field = SendMessageRequest.DESCRIPTOR.fields_by_name['message']
    schema = field_schema(field, {})
    assert '$ref' in schema
    assert 'oneOf' not in schema


def test_field_schema_repeated_optional_message_is_array_not_nullable():
    # Repeated non-REQUIRED message fields must be wrapped as an array, not
    # returned early as a nullable oneOf — the is_repeated check must come
    # first. Task.history is a real repeated, non-required message field.
    from a2a.types.a2a_pb2 import Task

    field = Task.DESCRIPTOR.fields_by_name['history']
    schema = field_schema(field, {})
    assert schema['type'] == 'array'
    assert 'oneOf' not in schema
    assert '$ref' in schema['items']


def test_message_schema_oneof_example_uses_first_variant_only():
    components = {}
    message_schema(Part.DESCRIPTOR, components)
    example = components['Part']['example']
    assert example == {'text': ''}
    # base properties (metadata, filename, media_type) must not appear in the
    # example — they are objects/strings that would be wrong if sent as "".
    assert 'metadata' not in example
    assert 'filename' not in example


def test_field_schema_repeated_ref_example_propagated():
    components = {}
    msg_descriptor = SendMessageRequest.DESCRIPTOR.fields_by_name[
        'message'
    ].message_type
    parts_field = msg_descriptor.fields_by_name['parts']
    schema = field_schema(parts_field, components)
    assert schema['type'] == 'array'
    assert schema['example'] == [{'text': ''}]


def test_field_schema_map_entry():
    metadata_field = SendMessageRequest.DESCRIPTOR.fields_by_name['metadata']
    schema = field_schema(metadata_field, {})
    assert schema == {'type': 'object'}


def test_rest_body_types_coverage():
    assert ('/message:send', 'POST') in REST_BODY_TYPES
    assert ('/message:stream', 'POST') in REST_BODY_TYPES
    assert ('/tasks/{id}/pushNotificationConfigs', 'POST') in REST_BODY_TYPES


def test_full_schema_builds_for_all_rest_body_types():
    # Safety net: build the complete schema for every registered REST body
    # type into a shared components dict. Any proto field structure we don't
    # support (or stop supporting after a proto change) fails right here
    # rather than silently producing a broken Swagger document.
    components: dict = {}
    for msg in REST_BODY_TYPES.values():
        ref = message_schema(msg.DESCRIPTOR, components)
        assert ref['$ref'].startswith('#/components/schemas/')

    # Every registered schema must be a non-empty object/composition (the
    # cyclic-type placeholder is filled in before the build returns).
    for name, schema in components.items():
        assert schema, f'{name} resolved to an empty schema'
        assert 'type' in schema or 'allOf' in schema or '$ref' in schema
