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
