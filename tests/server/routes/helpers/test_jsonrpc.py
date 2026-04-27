from a2a.server.routes.helpers.jsonrpc import (
    DESCRIPTION,
    METHOD_TYPES,
    envelope_schema,
)


def test_envelope_schema_ref():
    components = {}
    ref = envelope_schema(components)
    assert ref == {'$ref': '#/components/schemas/A2ARequest'}


def test_envelope_schema_required_fields():
    components = {}
    envelope_schema(components)
    assert components['A2ARequest']['required'] == ['jsonrpc', 'method']


def test_envelope_schema_method_enum_matches_method_types():
    components = {}
    envelope_schema(components)
    enum = components['A2ARequest']['properties']['method']['enum']
    assert set(enum) == set(METHOD_TYPES)


def test_envelope_schema_params_is_one_of():
    components = {}
    envelope_schema(components)
    params = components['A2ARequest']['properties']['params']
    assert 'oneOf' in params
    assert len(params['oneOf']) > 0


def test_envelope_schema_deduplicates_shared_param_types():
    # SendMessage and SendStreamingMessage share SendMessageRequest.
    components = {}
    envelope_schema(components)
    refs = [
        r['$ref']
        for r in components['A2ARequest']['properties']['params']['oneOf']
    ]
    assert len(refs) == len(set(refs))


def test_envelope_schema_jsonrpc_version():
    components = {}
    envelope_schema(components)
    assert components['A2ARequest']['properties']['jsonrpc']['enum'] == ['2.0']


def test_method_types_contains_all_a2a_methods():
    expected = {
        'SendMessage',
        'SendStreamingMessage',
        'GetTask',
        'ListTasks',
        'CancelTask',
        'CreateTaskPushNotificationConfig',
        'GetTaskPushNotificationConfig',
        'ListTaskPushNotificationConfigs',
        'DeleteTaskPushNotificationConfig',
        'SubscribeToTask',
        'GetExtendedAgentCard',
    }
    assert set(METHOD_TYPES) == expected


def test_description_lists_all_methods():
    for method in METHOD_TYPES:
        assert method in DESCRIPTION
