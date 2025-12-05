from unittest import mock

import pytest

from a2a import types
from a2a.grpc import a2a_pb2
from a2a.utils import proto_utils
from a2a.utils.errors import ServerError


# --- Test Data ---


@pytest.fixture
def sample_message() -> types.Message:
    return types.Message(
        message_id='msg-1',
        context_id='ctx-1',
        task_id='task-1',
        role=types.Role.user,
        parts=[
            types.Part(root=types.TextPart(text='Hello')),
            types.Part(
                root=types.FilePart(
                    file=types.FileWithUri(
                        uri='file:///test.txt',
                        name='test.txt',
                        mime_type='text/plain',
                    ),
                )
            ),
            types.Part(root=types.DataPart(data={'key': 'value'})),
        ],
        metadata={'source': 'test'},
    )


@pytest.fixture
def sample_task(sample_message: types.Message) -> types.Task:
    return types.Task(
        id='task-1',
        context_id='ctx-1',
        status=types.TaskStatus(
            state=types.TaskState.working, message=sample_message
        ),
        history=[sample_message],
        artifacts=[
            types.Artifact(
                artifact_id='art-1',
                parts=[
                    types.Part(root=types.TextPart(text='Artifact content'))
                ],
            )
        ],
        metadata={'source': 'test'},
    )


@pytest.fixture
def sample_agent_card() -> types.AgentCard:
    return types.AgentCard(
        name='Test Agent',
        description='A test agent',
        url='http://localhost',
        version='1.0.0',
        capabilities=types.AgentCapabilities(
            streaming=True, push_notifications=True
        ),
        default_input_modes=['text/plain'],
        default_output_modes=['text/plain'],
        skills=[
            types.AgentSkill(
                id='skill1',
                name='Test Skill',
                description='A test skill',
                tags=['test'],
            )
        ],
        provider=types.AgentProvider(
            organization='Test Org', url='http://test.org'
        ),
        security=[{'oauth_scheme': ['read', 'write']}],
        security_schemes={
            'oauth_scheme': types.SecurityScheme(
                root=types.OAuth2SecurityScheme(
                    flows=types.OAuthFlows(
                        client_credentials=types.ClientCredentialsOAuthFlow(
                            token_url='http://token.url',
                            scopes={
                                'read': 'Read access',
                                'write': 'Write access',
                            },
                        )
                    )
                )
            ),
            'apiKey': types.SecurityScheme(
                root=types.APIKeySecurityScheme(
                    name='X-API-KEY', in_=types.In.header
                )
            ),
            'httpAuth': types.SecurityScheme(
                root=types.HTTPAuthSecurityScheme(scheme='bearer')
            ),
            'oidc': types.SecurityScheme(
                root=types.OpenIdConnectSecurityScheme(
                    open_id_connect_url='http://oidc.url'
                )
            ),
        },
        signatures=[
            types.AgentCardSignature(
                protected='protected_test',
                signature='signature_test',
                header={'alg': 'ES256'},
            ),
            types.AgentCardSignature(
                protected='protected_val',
                signature='signature_val',
                header={'alg': 'ES256', 'kid': 'unique-key-identifier-123'},
            ),
        ],
    )


# --- Test Cases ---


class TestToProto:
    def test_part_unsupported_type(self):
        """Test that ToProto.part raises ValueError for an unsupported Part type."""

        class FakePartType:
            kind = 'fake'

        # Create a mock Part object that has a .root attribute pointing to the fake type
        mock_part = mock.MagicMock(spec=types.Part)
        mock_part.root = FakePartType()

        with pytest.raises(ValueError, match='Unsupported part type'):
            proto_utils.ToProto.part(mock_part)


class TestFromProto:
    def test_part_unsupported_type(self):
        """Test that FromProto.part raises ValueError for an unsupported part type in proto."""
        unsupported_proto_part = (
            a2a_pb2.Part()
        )  # An empty part with no oneof field set
        with pytest.raises(ValueError, match='Unsupported part type'):
            proto_utils.FromProto.part(unsupported_proto_part)

    def test_task_query_params_invalid_name(self):
        request = a2a_pb2.GetTaskRequest(name='invalid-name-format')
        with pytest.raises(ServerError) as exc_info:
            proto_utils.FromProto.task_query_params(request)
        assert isinstance(exc_info.value.error, types.InvalidParamsError)


class TestProtoUtils:
    def test_roundtrip_message(self, sample_message: types.Message):
        """Test conversion of Message to proto and back."""
        proto_msg = proto_utils.ToProto.message(sample_message)
        assert isinstance(proto_msg, a2a_pb2.Message)

        # Test file part handling
        assert proto_msg.content[1].file.file_with_uri == 'file:///test.txt'
        assert proto_msg.content[1].file.mime_type == 'text/plain'
        assert proto_msg.content[1].file.name == 'test.txt'

        roundtrip_msg = proto_utils.FromProto.message(proto_msg)
        assert roundtrip_msg == sample_message

    def test_enum_conversions(self):
        """Test conversions for all enum types."""
        assert (
            proto_utils.ToProto.role(types.Role.agent)
            == a2a_pb2.Role.ROLE_AGENT
        )
        assert (
            proto_utils.FromProto.role(a2a_pb2.Role.ROLE_USER)
            == types.Role.user
        )

        for state in types.TaskState:
            if state not in (types.TaskState.unknown, types.TaskState.rejected):
                proto_state = proto_utils.ToProto.task_state(state)
                assert proto_utils.FromProto.task_state(proto_state) == state

        # Test unknown state case
        assert (
            proto_utils.FromProto.task_state(
                a2a_pb2.TaskState.TASK_STATE_UNSPECIFIED
            )
            == types.TaskState.unknown
        )
        assert (
            proto_utils.ToProto.task_state(types.TaskState.unknown)
            == a2a_pb2.TaskState.TASK_STATE_UNSPECIFIED
        )

    def test_oauth_flows_conversion(self):
        """Test conversion of different OAuth2 flows."""
        # Test password flow
        password_flow = types.OAuthFlows(
            password=types.PasswordOAuthFlow(
                token_url='http://token.url', scopes={'read': 'Read'}
            )
        )
        proto_password_flow = proto_utils.ToProto.oauth2_flows(password_flow)
        assert proto_password_flow.HasField('password')

        # Test implicit flow
        implicit_flow = types.OAuthFlows(
            implicit=types.ImplicitOAuthFlow(
                authorization_url='http://auth.url', scopes={'read': 'Read'}
            )
        )
        proto_implicit_flow = proto_utils.ToProto.oauth2_flows(implicit_flow)
        assert proto_implicit_flow.HasField('implicit')

        # Test authorization code flow
        auth_code_flow = types.OAuthFlows(
            authorization_code=types.AuthorizationCodeOAuthFlow(
                authorization_url='http://auth.url',
                token_url='http://token.url',
                scopes={'read': 'read'},
            )
        )
        proto_auth_code_flow = proto_utils.ToProto.oauth2_flows(auth_code_flow)
        assert proto_auth_code_flow.HasField('authorization_code')

        # Test invalid flow
        with pytest.raises(ValueError):
            proto_utils.ToProto.oauth2_flows(types.OAuthFlows())

        # Test FromProto
        roundtrip_password = proto_utils.FromProto.oauth2_flows(
            proto_password_flow
        )
        assert roundtrip_password.password is not None

        roundtrip_implicit = proto_utils.FromProto.oauth2_flows(
            proto_implicit_flow
        )
        assert roundtrip_implicit.implicit is not None

    def test_task_id_params_from_proto_invalid_name(self):
        request = a2a_pb2.CancelTaskRequest(name='invalid-name-format')
        with pytest.raises(ServerError) as exc_info:
            proto_utils.FromProto.task_id_params(request)
        assert isinstance(exc_info.value.error, types.InvalidParamsError)

    def test_task_push_config_from_proto_invalid_parent(self):
        request = a2a_pb2.TaskPushNotificationConfig(name='invalid-name-format')
        with pytest.raises(ServerError) as exc_info:
            proto_utils.FromProto.task_push_notification_config(request)
        assert isinstance(exc_info.value.error, types.InvalidParamsError)

    def test_none_handling(self):
        """Test that None inputs are handled gracefully."""
        assert proto_utils.ToProto.message(None) is None
        assert proto_utils.ToProto.metadata(None) is None
        assert proto_utils.ToProto.provider(None) is None
        assert proto_utils.ToProto.security(None) is None
        assert proto_utils.ToProto.security_schemes(None) is None

    def test_metadata_conversion(self):
        """Test metadata conversion with various data types."""
        metadata = {
            'null_value': None,
            'bool_value': True,
            'int_value': 42,
            'float_value': 3.14,
            'string_value': 'hello',
            'dict_value': {'nested': 'dict', 'count': 10},
            'list_value': [1, 'two', 3.0, True, None],
            'tuple_value': (1, 2, 3),
            'complex_list': [
                {'name': 'item1', 'values': [1, 2, 3]},
                {'name': 'item2', 'values': [4, 5, 6]},
            ],
        }

        # Convert to proto
        proto_metadata = proto_utils.ToProto.metadata(metadata)
        assert proto_metadata is not None

        # Convert back to Python
        roundtrip_metadata = proto_utils.FromProto.metadata(proto_metadata)

        # Verify all values are preserved correctly
        assert roundtrip_metadata['null_value'] is None
        assert roundtrip_metadata['bool_value'] is True
        assert roundtrip_metadata['int_value'] == 42
        assert roundtrip_metadata['float_value'] == 3.14
        assert roundtrip_metadata['string_value'] == 'hello'
        assert roundtrip_metadata['dict_value']['nested'] == 'dict'
        assert roundtrip_metadata['dict_value']['count'] == 10
        assert roundtrip_metadata['list_value'] == [1, 'two', 3.0, True, None]
        assert roundtrip_metadata['tuple_value'] == [
            1,
            2,
            3,
        ]  # tuples become lists
        assert len(roundtrip_metadata['complex_list']) == 2
        assert roundtrip_metadata['complex_list'][0]['name'] == 'item1'

    def test_metadata_with_custom_objects(self):
        """Test metadata conversion with custom objects using preprocessing utility."""

        class CustomObject:
            def __str__(self):
                return 'custom_object_str'

            def __repr__(self):
                return 'CustomObject()'

        metadata = {
            'custom_obj': CustomObject(),
            'list_with_custom': [1, CustomObject(), 'text'],
            'nested_custom': {'obj': CustomObject(), 'normal': 'value'},
        }

        # Use preprocessing utility to make it serializable
        serializable_metadata = proto_utils.make_dict_serializable(metadata)

        # Convert to proto
        proto_metadata = proto_utils.ToProto.metadata(serializable_metadata)
        assert proto_metadata is not None

        # Convert back to Python
        roundtrip_metadata = proto_utils.FromProto.metadata(proto_metadata)

        # Custom objects should be converted to strings
        assert roundtrip_metadata['custom_obj'] == 'custom_object_str'
        assert roundtrip_metadata['list_with_custom'] == [
            1,
            'custom_object_str',
            'text',
        ]
        assert roundtrip_metadata['nested_custom']['obj'] == 'custom_object_str'
        assert roundtrip_metadata['nested_custom']['normal'] == 'value'

    def test_metadata_edge_cases(self):
        """Test metadata conversion with edge cases."""
        metadata = {
            'empty_dict': {},
            'empty_list': [],
            'zero': 0,
            'false': False,
            'empty_string': '',
            'unicode_string': 'string test',
            'safe_number': 9007199254740991,  # JavaScript MAX_SAFE_INTEGER
            'negative_number': -42,
            'float_precision': 0.123456789,
            'numeric_string': '12345',
        }

        # Convert to proto and back
        proto_metadata = proto_utils.ToProto.metadata(metadata)
        roundtrip_metadata = proto_utils.FromProto.metadata(proto_metadata)

        # Verify edge cases are handled correctly
        assert roundtrip_metadata['empty_dict'] == {}
        assert roundtrip_metadata['empty_list'] == []
        assert roundtrip_metadata['zero'] == 0
        assert roundtrip_metadata['false'] is False
        assert roundtrip_metadata['empty_string'] == ''
        assert roundtrip_metadata['unicode_string'] == 'string test'
        assert roundtrip_metadata['safe_number'] == 9007199254740991
        assert roundtrip_metadata['negative_number'] == -42
        assert abs(roundtrip_metadata['float_precision'] - 0.123456789) < 1e-10
        assert roundtrip_metadata['numeric_string'] == '12345'

    def test_make_dict_serializable(self):
        """Test the make_dict_serializable utility function."""

        class CustomObject:
            def __str__(self):
                return 'custom_str'

        test_data = {
            'string': 'hello',
            'int': 42,
            'float': 3.14,
            'bool': True,
            'none': None,
            'custom': CustomObject(),
            'list': [1, 'two', CustomObject()],
            'tuple': (1, 2, CustomObject()),
            'nested': {'inner_custom': CustomObject(), 'inner_normal': 'value'},
        }

        result = proto_utils.make_dict_serializable(test_data)

        # Basic types should be unchanged
        assert result['string'] == 'hello'
        assert result['int'] == 42
        assert result['float'] == 3.14
        assert result['bool'] is True
        assert result['none'] is None

        # Custom objects should be converted to strings
        assert result['custom'] == 'custom_str'
        assert result['list'] == [1, 'two', 'custom_str']
        assert result['tuple'] == [1, 2, 'custom_str']  # tuples become lists
        assert result['nested']['inner_custom'] == 'custom_str'
        assert result['nested']['inner_normal'] == 'value'

    def test_normalize_large_integers_to_strings(self):
        """Test the normalize_large_integers_to_strings utility function."""

        test_data = {
            'small_int': 42,
            'large_int': 9999999999999999999,  # > 15 digits
            'negative_large': -9999999999999999999,
            'float': 3.14,
            'string': 'hello',
            'list': [123, 9999999999999999999, 'text'],
            'nested': {'inner_large': 9999999999999999999, 'inner_small': 100},
        }

        result = proto_utils.normalize_large_integers_to_strings(test_data)

        # Small integers should remain as integers
        assert result['small_int'] == 42
        assert isinstance(result['small_int'], int)

        # Large integers should be converted to strings
        assert result['large_int'] == '9999999999999999999'
        assert isinstance(result['large_int'], str)
        assert result['negative_large'] == '-9999999999999999999'
        assert isinstance(result['negative_large'], str)

        # Other types should be unchanged
        assert result['float'] == 3.14
        assert result['string'] == 'hello'

        # Lists should be processed recursively
        assert result['list'] == [123, '9999999999999999999', 'text']

        # Nested dicts should be processed recursively
        assert result['nested']['inner_large'] == '9999999999999999999'
        assert result['nested']['inner_small'] == 100

    def test_parse_string_integers_in_dict(self):
        """Test the parse_string_integers_in_dict utility function."""

        test_data = {
            'regular_string': 'hello',
            'numeric_string_small': '123',  # small, should stay as string
            'numeric_string_large': '9999999999999999999',  # > 15 digits, should become int
            'negative_large_string': '-9999999999999999999',
            'float_string': '3.14',  # not all digits, should stay as string
            'mixed_string': '123abc',  # not all digits, should stay as string
            'int': 42,
            'list': ['hello', '9999999999999999999', '123'],
            'nested': {
                'inner_large_string': '9999999999999999999',
                'inner_regular': 'value',
            },
        }

        result = proto_utils.parse_string_integers_in_dict(test_data)

        # Regular strings should remain unchanged
        assert result['regular_string'] == 'hello'
        assert (
            result['numeric_string_small'] == '123'
        )  # too small, stays string
        assert result['float_string'] == '3.14'  # not all digits
        assert result['mixed_string'] == '123abc'  # not all digits

        # Large numeric strings should be converted to integers
        assert result['numeric_string_large'] == 9999999999999999999
        assert isinstance(result['numeric_string_large'], int)
        assert result['negative_large_string'] == -9999999999999999999
        assert isinstance(result['negative_large_string'], int)

        # Other types should be unchanged
        assert result['int'] == 42

        # Lists should be processed recursively
        assert result['list'] == ['hello', 9999999999999999999, '123']

        # Nested dicts should be processed recursively
        assert result['nested']['inner_large_string'] == 9999999999999999999
        assert result['nested']['inner_regular'] == 'value'

    def test_large_integer_roundtrip_with_utilities(self):
        """Test large integer handling with preprocessing and post-processing utilities."""

        original_data = {
            'large_int': 9999999999999999999,
            'small_int': 42,
            'nested': {'another_large': 12345678901234567890, 'normal': 'text'},
        }

        # Step 1: Preprocess to convert large integers to strings
        preprocessed = proto_utils.normalize_large_integers_to_strings(
            original_data
        )

        # Step 2: Convert to proto
        proto_metadata = proto_utils.ToProto.metadata(preprocessed)
        assert proto_metadata is not None

        # Step 3: Convert back from proto
        dict_from_proto = proto_utils.FromProto.metadata(proto_metadata)

        # Step 4: Post-process to convert large integer strings back to integers
        final_result = proto_utils.parse_string_integers_in_dict(
            dict_from_proto
        )

        # Verify roundtrip preserved the original data
        assert final_result['large_int'] == 9999999999999999999
        assert isinstance(final_result['large_int'], int)
        assert final_result['small_int'] == 42
        assert final_result['nested']['another_large'] == 12345678901234567890
        assert isinstance(final_result['nested']['another_large'], int)
        assert final_result['nested']['normal'] == 'text'

    def test_task_conversion_roundtrip(
        self, sample_task: types.Task, sample_message: types.Message
    ):
        """Test conversion of Task to proto and back."""
        proto_task = proto_utils.ToProto.task(sample_task)
        assert isinstance(proto_task, a2a_pb2.Task)

        roundtrip_task = proto_utils.FromProto.task(proto_task)
        assert roundtrip_task.id == 'task-1'
        assert roundtrip_task.context_id == 'ctx-1'
        assert roundtrip_task.status == types.TaskStatus(
            state=types.TaskState.working, message=sample_message
        )
        assert roundtrip_task.history == sample_task.history
        assert roundtrip_task.artifacts == [
            types.Artifact(
                artifact_id='art-1',
                description='',
                metadata={},
                name='',
                parts=[
                    types.Part(root=types.TextPart(text='Artifact content'))
                ],
            )
        ]
        assert roundtrip_task.metadata == {'source': 'test'}

    def test_agent_card_conversion_roundtrip(
        self, sample_agent_card: types.AgentCard
    ):
        """Test conversion of AgentCard to proto and back."""
        proto_card = proto_utils.ToProto.agent_card(sample_agent_card)
        assert isinstance(proto_card, a2a_pb2.AgentCard)

        roundtrip_card = proto_utils.FromProto.agent_card(proto_card)
        assert roundtrip_card.name == 'Test Agent'
        assert roundtrip_card.description == 'A test agent'
        assert roundtrip_card.url == 'http://localhost'
        assert roundtrip_card.version == '1.0.0'
        assert roundtrip_card.capabilities == types.AgentCapabilities(
            extensions=[], streaming=True, push_notifications=True
        )
        assert roundtrip_card.default_input_modes == ['text/plain']
        assert roundtrip_card.default_output_modes == ['text/plain']
        assert roundtrip_card.skills == [
            types.AgentSkill(
                id='skill1',
                name='Test Skill',
                description='A test skill',
                tags=['test'],
                examples=[],
                input_modes=[],
                output_modes=[],
            )
        ]
        assert roundtrip_card.provider == types.AgentProvider(
            organization='Test Org', url='http://test.org'
        )
        assert roundtrip_card.security == [{'oauth_scheme': ['read', 'write']}]

        # Normalized version of security_schemes. None fields are filled with defaults.
        expected_security_schemes = {
            'oauth_scheme': types.SecurityScheme(
                root=types.OAuth2SecurityScheme(
                    description='',
                    flows=types.OAuthFlows(
                        client_credentials=types.ClientCredentialsOAuthFlow(
                            refresh_url='',
                            scopes={
                                'write': 'Write access',
                                'read': 'Read access',
                            },
                            token_url='http://token.url',
                        ),
                    ),
                )
            ),
            'apiKey': types.SecurityScheme(
                root=types.APIKeySecurityScheme(
                    description='',
                    in_=types.In.header,
                    name='X-API-KEY',
                )
            ),
            'httpAuth': types.SecurityScheme(
                root=types.HTTPAuthSecurityScheme(
                    bearer_format='',
                    description='',
                    scheme='bearer',
                )
            ),
            'oidc': types.SecurityScheme(
                root=types.OpenIdConnectSecurityScheme(
                    description='',
                    open_id_connect_url='http://oidc.url',
                )
            ),
        }
        assert roundtrip_card.security_schemes == expected_security_schemes
        assert roundtrip_card.signatures == [
            types.AgentCardSignature(
                protected='protected_test',
                signature='signature_test',
                header={'alg': 'ES256'},
            ),
            types.AgentCardSignature(
                protected='protected_val',
                signature='signature_val',
                header={'alg': 'ES256', 'kid': 'unique-key-identifier-123'},
            ),
        ]

    @pytest.mark.parametrize(
        'signature_data, expected_data',
        [
            (
                types.AgentCardSignature(
                    protected='protected_val',
                    signature='signature_val',
                    header={'alg': 'ES256'},
                ),
                types.AgentCardSignature(
                    protected='protected_val',
                    signature='signature_val',
                    header={'alg': 'ES256'},
                ),
            ),
            (
                types.AgentCardSignature(
                    protected='protected_val',
                    signature='signature_val',
                    header=None,
                ),
                types.AgentCardSignature(
                    protected='protected_val',
                    signature='signature_val',
                    header={},
                ),
            ),
            (
                types.AgentCardSignature(
                    protected='',
                    signature='',
                    header={},
                ),
                types.AgentCardSignature(
                    protected='',
                    signature='',
                    header={},
                ),
            ),
        ],
    )
    def test_agent_card_signature_conversion_roundtrip(
        self, signature_data, expected_data
    ):
        """Test conversion of AgentCardSignature to proto and back."""
        proto_signature = proto_utils.ToProto.agent_card_signature(
            signature_data
        )
        assert isinstance(proto_signature, a2a_pb2.AgentCardSignature)
        roundtrip_signature = proto_utils.FromProto.agent_card_signature(
            proto_signature
        )
        assert roundtrip_signature == expected_data
