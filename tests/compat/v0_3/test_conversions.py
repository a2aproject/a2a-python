import base64

import pytest

from google.protobuf.json_format import ParseDict
import json

from a2a.compat.v0_3 import types as types_v03
from a2a.compat.v0_3.conversions import (
    to_compat_agent_capabilities,
    to_compat_agent_card,
    to_compat_agent_card_signature,
    to_compat_agent_extension,
    to_compat_agent_interface,
    to_compat_agent_provider,
    to_compat_agent_skill,
    to_compat_artifact,
    to_compat_authentication_info,
    to_compat_cancel_task_request,
    to_compat_create_task_push_notification_config_request,
    to_compat_delete_task_push_notification_config_request,
    to_compat_get_extended_agent_card_request,
    to_compat_get_task_push_notification_config_request,
    to_compat_get_task_request,
    to_compat_list_task_push_notification_config_request,
    to_compat_list_task_push_notification_config_response,
    to_compat_message,
    to_compat_oauth_flows,
    to_compat_part,
    to_compat_push_notification_config,
    to_compat_security_requirement,
    to_compat_security_scheme,
    to_compat_send_message_configuration,
    to_compat_send_message_request,
    to_compat_send_message_response,
    to_compat_stream_response,
    to_compat_subscribe_to_task_request,
    to_compat_task,
    to_compat_task_artifact_update_event,
    to_compat_task_push_notification_config,
    to_compat_task_status,
    to_compat_task_status_update_event,
    to_core_agent_capabilities,
    to_core_agent_card,
    to_core_agent_card_signature,
    to_core_agent_extension,
    to_core_agent_interface,
    to_core_agent_provider,
    to_core_agent_skill,
    to_core_artifact,
    to_core_authentication_info,
    to_core_cancel_task_request,
    to_core_create_task_push_notification_config_request,
    to_core_delete_task_push_notification_config_request,
    to_core_get_extended_agent_card_request,
    to_core_get_task_push_notification_config_request,
    to_core_get_task_request,
    to_core_list_task_push_notification_config_request,
    to_core_list_task_push_notification_config_response,
    to_core_message,
    to_core_oauth_flows,
    to_core_part,
    to_core_push_notification_config,
    to_core_security_requirement,
    to_core_security_scheme,
    to_core_send_message_configuration,
    to_core_send_message_request,
    to_core_send_message_response,
    to_core_stream_response,
    to_core_subscribe_to_task_request,
    to_core_task,
    to_core_task_artifact_update_event,
    to_core_task_push_notification_config,
    to_core_task_status,
    to_core_task_status_update_event,
)
from a2a.compat.v0_3.model_conversions import (
    core_to_compat_task_model,
    compat_task_model_to_core,
    core_to_compat_push_notification_config_model,
    compat_push_notification_config_model_to_core,
)
from a2a.server.models import PushNotificationConfigModel, TaskModel
from cryptography.fernet import Fernet
from a2a.types import a2a_pb2 as pb2_v10
from a2a.utils.errors import VersionNotSupportedError


def test_text_part_conversion():
    v03_part = types_v03.Part(
        root=types_v03.TextPart(text='Hello, World!', metadata={'test': 'val'})
    )
    v10_expected = pb2_v10.Part(text='Hello, World!')
    v10_expected.metadata.update({'test': 'val'})

    v10_part = to_core_part(v03_part)
    assert v10_part == v10_expected

    v03_restored = to_compat_part(v10_part)
    assert v03_restored == v03_part


def test_data_part_conversion():
    data = {'key': 'val', 'nested': {'a': 1}}
    v03_part = types_v03.Part(root=types_v03.DataPart(data=data))
    v10_expected = pb2_v10.Part()
    ParseDict(data, v10_expected.data.struct_value)

    v10_part = to_core_part(v03_part)
    assert v10_part == v10_expected

    v03_restored = to_compat_part(v10_part)
    assert v03_restored == v03_part


def test_data_part_conversion_primitive():
    primitive_cases = [
        'Primitive String',
        42,
        3.14,
        True,
        False,
        ['a', 'b', 'c'],
        [1, 2, 3],
        None,
    ]

    for val in primitive_cases:
        v10_expected = pb2_v10.Part()
        ParseDict(val, v10_expected.data)

        # Test v10 -> v03
        v03_part = to_compat_part(v10_expected)
        assert isinstance(v03_part.root, types_v03.DataPart)
        assert v03_part.root.data == {'value': val}
        assert v03_part.root.metadata['data_part_compat'] is True

        # Test v03 -> v10
        v10_restored = to_core_part(v03_part)
        assert v10_restored == v10_expected


def test_file_part_uri_conversion():
    v03_file = types_v03.FileWithUri(
        uri='http://example.com/file', mime_type='text/plain', name='file.txt'
    )
    v03_part = types_v03.Part(root=types_v03.FilePart(file=v03_file))
    v10_expected = pb2_v10.Part(
        url='http://example.com/file',
        media_type='text/plain',
        filename='file.txt',
    )

    v10_part = to_core_part(v03_part)
    assert v10_part == v10_expected

    v03_restored = to_compat_part(v10_part)
    assert v03_restored == v03_part


def test_file_part_bytes_conversion():
    content = b'hello world'
    b64 = base64.b64encode(content).decode('utf-8')
    v03_file = types_v03.FileWithBytes(
        bytes=b64, mime_type='application/octet-stream', name='file.bin'
    )
    v03_part = types_v03.Part(root=types_v03.FilePart(file=v03_file))
    v10_expected = pb2_v10.Part(
        raw=content, media_type='application/octet-stream', filename='file.bin'
    )

    v10_part = to_core_part(v03_part)
    assert v10_part == v10_expected

    v03_restored = to_compat_part(v10_part)
    assert v03_restored == v03_part


def test_message_conversion():
    v03_msg = types_v03.Message(
        message_id='m1',
        role=types_v03.Role.user,
        context_id='c1',
        task_id='t1',
        reference_task_ids=['rt1'],
        metadata={'k': 'v'},
        extensions=['ext1'],
        parts=[types_v03.Part(root=types_v03.TextPart(text='hi'))],
    )
    v10_expected = pb2_v10.Message(
        message_id='m1',
        role=pb2_v10.Role.ROLE_USER,
        context_id='c1',
        task_id='t1',
        reference_task_ids=['rt1'],
        extensions=['ext1'],
        parts=[pb2_v10.Part(text='hi')],
    )
    ParseDict({'k': 'v'}, v10_expected.metadata)

    v10_msg = to_core_message(v03_msg)
    assert v10_msg == v10_expected

    v03_restored = to_compat_message(v10_msg)
    assert v03_restored == v03_msg


def test_message_conversion_minimal():
    v03_msg = types_v03.Message(
        message_id='m1',
        role=types_v03.Role.agent,
        parts=[types_v03.Part(root=types_v03.TextPart(text='hi'))],
    )
    v10_expected = pb2_v10.Message(
        message_id='m1',
        role=pb2_v10.Role.ROLE_AGENT,
        parts=[pb2_v10.Part(text='hi')],
    )

    v10_msg = to_core_message(v03_msg)
    assert v10_msg == v10_expected

    v03_restored = to_compat_message(v10_msg)
    # v03 expects None for missing fields, conversions.py handles this correctly
    assert v03_restored == v03_msg


def test_task_status_conversion():
    now_v03 = '2023-01-01T12:00:00Z'
    v03_msg = types_v03.Message(
        message_id='m1',
        role=types_v03.Role.agent,
        parts=[types_v03.Part(root=types_v03.TextPart(text='status'))],
    )
    v03_status = types_v03.TaskStatus(
        state=types_v03.TaskState.working, message=v03_msg, timestamp=now_v03
    )

    v10_expected = pb2_v10.TaskStatus(
        state=pb2_v10.TaskState.TASK_STATE_WORKING,
        message=pb2_v10.Message(
            message_id='m1',
            role=pb2_v10.Role.ROLE_AGENT,
            parts=[pb2_v10.Part(text='status')],
        ),
    )
    v10_expected.timestamp.FromJsonString(now_v03)

    v10_status = to_core_task_status(v03_status)
    assert v10_status == v10_expected

    v03_restored = to_compat_task_status(v10_status)
    assert v03_restored == v03_status


def test_task_status_conversion_special_states():
    # input-required
    s1 = types_v03.TaskStatus(state=types_v03.TaskState.input_required)
    assert (
        to_core_task_status(s1).state
        == pb2_v10.TaskState.TASK_STATE_INPUT_REQUIRED
    )
    assert to_compat_task_status(to_core_task_status(s1)).state == s1.state

    # auth-required
    s2 = types_v03.TaskStatus(state=types_v03.TaskState.auth_required)
    assert (
        to_core_task_status(s2).state
        == pb2_v10.TaskState.TASK_STATE_AUTH_REQUIRED
    )
    assert to_compat_task_status(to_core_task_status(s2)).state == s2.state

    # unknown
    s3 = types_v03.TaskStatus(state=types_v03.TaskState.unknown)
    assert (
        to_core_task_status(s3).state
        == pb2_v10.TaskState.TASK_STATE_UNSPECIFIED
    )
    assert to_compat_task_status(to_core_task_status(s3)).state == s3.state


def test_task_conversion():
    v03_msg = types_v03.Message(
        message_id='m1',
        role=types_v03.Role.user,
        parts=[types_v03.Part(root=types_v03.TextPart(text='hi'))],
    )
    v03_status = types_v03.TaskStatus(state=types_v03.TaskState.submitted)
    v03_art = types_v03.Artifact(
        artifact_id='a1',
        parts=[types_v03.Part(root=types_v03.TextPart(text='data'))],
    )

    v03_task = types_v03.Task(
        id='t1',
        context_id='c1',
        status=v03_status,
        history=[v03_msg],
        artifacts=[v03_art],
        metadata={'m': 'v'},
    )

    v10_expected = pb2_v10.Task(
        id='t1',
        context_id='c1',
        status=pb2_v10.TaskStatus(state=pb2_v10.TaskState.TASK_STATE_SUBMITTED),
        history=[
            pb2_v10.Message(
                message_id='m1',
                role=pb2_v10.Role.ROLE_USER,
                parts=[pb2_v10.Part(text='hi')],
            )
        ],
        artifacts=[
            pb2_v10.Artifact(
                artifact_id='a1', parts=[pb2_v10.Part(text='data')]
            )
        ],
    )
    ParseDict({'m': 'v'}, v10_expected.metadata)

    v10_task = to_core_task(v03_task)
    assert v10_task == v10_expected

    v03_restored = to_compat_task(v10_task)
    # v03 restored artifacts will have None for name/desc/etc
    v03_expected_restored = types_v03.Task(
        id='t1',
        context_id='c1',
        status=v03_status,
        history=[v03_msg],
        artifacts=[
            types_v03.Artifact(
                artifact_id='a1',
                parts=[types_v03.Part(root=types_v03.TextPart(text='data'))],
                name=None,
                description=None,
                metadata=None,
                extensions=None,
            )
        ],
        metadata={'m': 'v'},
    )
    assert v03_restored == v03_expected_restored


def test_task_conversion_minimal():
    # Test v10 to v03 minimal
    v10_min = pb2_v10.Task(id='tm', context_id='cm')
    v03_expected_restored = types_v03.Task(
        id='tm',
        context_id='cm',
        status=types_v03.TaskStatus(state=types_v03.TaskState.unknown),
    )
    v03_min_restored = to_compat_task(v10_min)
    assert v03_min_restored == v03_expected_restored


def test_authentication_info_conversion():
    v03_auth = types_v03.PushNotificationAuthenticationInfo(
        schemes=['Bearer'], credentials='token123'
    )
    v10_expected = pb2_v10.AuthenticationInfo(
        scheme='Bearer', credentials='token123'
    )
    v10_auth = to_core_authentication_info(v03_auth)
    assert v10_auth == v10_expected

    v03_restored = to_compat_authentication_info(v10_auth)
    assert v03_restored == v03_auth


def test_authentication_info_conversion_minimal():
    v03_auth = types_v03.PushNotificationAuthenticationInfo(schemes=[])
    v10_expected = pb2_v10.AuthenticationInfo()

    v10_auth = to_core_authentication_info(v03_auth)
    assert v10_auth == v10_expected

    v03_restored = to_compat_authentication_info(v10_auth)
    v03_expected_restored = types_v03.PushNotificationAuthenticationInfo(
        schemes=[], credentials=None
    )
    assert v03_restored == v03_expected_restored


def test_push_notification_config_conversion():
    v03_auth = types_v03.PushNotificationAuthenticationInfo(schemes=['Basic'])
    v03_config = types_v03.PushNotificationConfig(
        id='c1',
        url='http://test.com',
        token='tok',  # noqa: S106
        authentication=v03_auth,
    )

    v10_expected = pb2_v10.TaskPushNotificationConfig(
        id='c1',
        url='http://test.com',
        token='tok',  # noqa: S106
        authentication=pb2_v10.AuthenticationInfo(scheme='Basic'),
    )

    v10_config = to_core_push_notification_config(v03_config)
    assert v10_config == v10_expected

    v03_restored = to_compat_push_notification_config(v10_config)
    assert v03_restored == v03_config


def test_push_notification_config_conversion_minimal():
    v03_config = types_v03.PushNotificationConfig(url='http://test.com')
    v10_expected = pb2_v10.TaskPushNotificationConfig(url='http://test.com')

    v10_config = to_core_push_notification_config(v03_config)
    assert v10_config == v10_expected

    v03_restored = to_compat_push_notification_config(v10_config)
    v03_expected_restored = types_v03.PushNotificationConfig(
        url='http://test.com', id=None, token=None, authentication=None
    )
    assert v03_restored == v03_expected_restored


def test_send_message_configuration_conversion():
    v03_auth = types_v03.PushNotificationAuthenticationInfo(schemes=['Basic'])
    v03_push = types_v03.PushNotificationConfig(
        url='http://test', authentication=v03_auth
    )

    v03_config = types_v03.MessageSendConfiguration(
        accepted_output_modes=['text/plain', 'application/json'],
        history_length=10,
        blocking=True,
        push_notification_config=v03_push,
    )

    v10_expected = pb2_v10.SendMessageConfiguration(
        accepted_output_modes=['text/plain', 'application/json'],
        history_length=10,
        task_push_notification_config=pb2_v10.TaskPushNotificationConfig(
            url='http://test',
            authentication=pb2_v10.AuthenticationInfo(scheme='Basic'),
        ),
    )

    v10_config = to_core_send_message_configuration(v03_config)
    assert v10_config == v10_expected

    v03_restored = to_compat_send_message_configuration(v10_config)
    assert v03_restored == v03_config


def test_send_message_configuration_conversion_minimal():
    v03_config = types_v03.MessageSendConfiguration()
    v10_expected = pb2_v10.SendMessageConfiguration()

    v10_config = to_core_send_message_configuration(v03_config)
    assert v10_config == v10_expected
    v03_restored = to_compat_send_message_configuration(v10_config)
    v03_expected_restored = types_v03.MessageSendConfiguration(
        accepted_output_modes=None,
        history_length=None,
        blocking=True,
        push_notification_config=None,
    )
    assert v03_restored == v03_expected_restored


def test_artifact_conversion_full():
    v03_artifact = types_v03.Artifact(
        artifact_id='a1',
        name='Test Art',
        description='A test artifact',
        parts=[types_v03.Part(root=types_v03.TextPart(text='data'))],
        metadata={'k': 'v'},
        extensions=['ext1'],
    )

    v10_expected = pb2_v10.Artifact(
        artifact_id='a1',
        name='Test Art',
        description='A test artifact',
        parts=[pb2_v10.Part(text='data')],
        extensions=['ext1'],
    )
    ParseDict({'k': 'v'}, v10_expected.metadata)

    v10_art = to_core_artifact(v03_artifact)
    assert v10_art == v10_expected

    v03_restored = to_compat_artifact(v10_art)
    assert v03_restored == v03_artifact


def test_artifact_conversion_minimal():
    v03_artifact = types_v03.Artifact(
        artifact_id='a1',
        parts=[types_v03.Part(root=types_v03.TextPart(text='data'))],
    )

    v10_expected = pb2_v10.Artifact(
        artifact_id='a1', parts=[pb2_v10.Part(text='data')]
    )

    v10_art = to_core_artifact(v03_artifact)
    assert v10_art == v10_expected

    v03_restored = to_compat_artifact(v10_art)
    v03_expected_restored = types_v03.Artifact(
        artifact_id='a1',
        parts=[types_v03.Part(root=types_v03.TextPart(text='data'))],
        name=None,
        description=None,
        metadata=None,
        extensions=None,
    )
    assert v03_restored == v03_expected_restored


def test_task_status_update_event_conversion():
    v03_status = types_v03.TaskStatus(state=types_v03.TaskState.completed)
    v03_event = types_v03.TaskStatusUpdateEvent(
        task_id='t1',
        context_id='c1',
        status=v03_status,
        metadata={'m': 'v'},
        final=True,
    )

    v10_expected = pb2_v10.TaskStatusUpdateEvent(
        task_id='t1',
        context_id='c1',
        status=pb2_v10.TaskStatus(state=pb2_v10.TaskState.TASK_STATE_COMPLETED),
    )
    ParseDict({'m': 'v'}, v10_expected.metadata)

    v10_event = to_core_task_status_update_event(v03_event)
    assert v10_event == v10_expected

    v03_restored = to_compat_task_status_update_event(v10_event)
    v03_expected_restored = types_v03.TaskStatusUpdateEvent(
        task_id='t1',
        context_id='c1',
        status=v03_status,
        metadata={'m': 'v'},
        final=True,  # final is computed based on status.state
    )
    assert v03_restored == v03_expected_restored


def test_task_status_update_event_conversion_terminal_states():
    # Test all terminal states result in final=True
    terminal_states = [
        (
            pb2_v10.TaskState.TASK_STATE_COMPLETED,
            types_v03.TaskState.completed,
        ),
        (pb2_v10.TaskState.TASK_STATE_CANCELED, types_v03.TaskState.canceled),
        (pb2_v10.TaskState.TASK_STATE_FAILED, types_v03.TaskState.failed),
        (pb2_v10.TaskState.TASK_STATE_REJECTED, types_v03.TaskState.rejected),
    ]

    for core_st, compat_st in terminal_states:
        v10_event = pb2_v10.TaskStatusUpdateEvent(
            status=pb2_v10.TaskStatus(state=core_st)
        )
        v03_restored = to_compat_task_status_update_event(v10_event)
        assert v03_restored.final is True
        assert v03_restored.status.state == compat_st

    # Test non-terminal states result in final=False
    non_terminal_states = [
        (
            pb2_v10.TaskState.TASK_STATE_SUBMITTED,
            types_v03.TaskState.submitted,
        ),
        (pb2_v10.TaskState.TASK_STATE_WORKING, types_v03.TaskState.working),
        (
            pb2_v10.TaskState.TASK_STATE_INPUT_REQUIRED,
            types_v03.TaskState.input_required,
        ),
        (
            pb2_v10.TaskState.TASK_STATE_AUTH_REQUIRED,
            types_v03.TaskState.auth_required,
        ),
        (
            pb2_v10.TaskState.TASK_STATE_UNSPECIFIED,
            types_v03.TaskState.unknown,
        ),
    ]

    for core_st, compat_st in non_terminal_states:
        v10_event = pb2_v10.TaskStatusUpdateEvent(
            status=pb2_v10.TaskStatus(state=core_st)
        )
        v03_restored = to_compat_task_status_update_event(v10_event)
        assert v03_restored.final is False
        assert v03_restored.status.state == compat_st


def test_task_status_update_event_conversion_minimal():
    # v03 status is required but might be constructed empty internally
    v10_event = pb2_v10.TaskStatusUpdateEvent(task_id='t1', context_id='c1')
    v03_restored = to_compat_task_status_update_event(v10_event)
    v03_expected = types_v03.TaskStatusUpdateEvent(
        task_id='t1',
        context_id='c1',
        status=types_v03.TaskStatus(state=types_v03.TaskState.unknown),
        final=False,
    )
    assert v03_restored == v03_expected


def test_task_artifact_update_event_conversion():
    v03_art = types_v03.Artifact(
        artifact_id='a1',
        parts=[types_v03.Part(root=types_v03.TextPart(text='d'))],
    )
    v03_event = types_v03.TaskArtifactUpdateEvent(
        task_id='t1',
        context_id='c1',
        artifact=v03_art,
        append=True,
        last_chunk=False,
        metadata={'k': 'v'},
    )

    v10_expected = pb2_v10.TaskArtifactUpdateEvent(
        task_id='t1',
        context_id='c1',
        artifact=pb2_v10.Artifact(
            artifact_id='a1', parts=[pb2_v10.Part(text='d')]
        ),
        append=True,
        last_chunk=False,
    )
    ParseDict({'k': 'v'}, v10_expected.metadata)

    v10_event = to_core_task_artifact_update_event(v03_event)
    assert v10_event == v10_expected

    v03_restored = to_compat_task_artifact_update_event(v10_event)
    assert v03_restored == v03_event


def test_task_artifact_update_event_conversion_minimal():
    v03_art = types_v03.Artifact(
        artifact_id='a1',
        parts=[types_v03.Part(root=types_v03.TextPart(text='d'))],
    )
    v03_event = types_v03.TaskArtifactUpdateEvent(
        task_id='t1', context_id='c1', artifact=v03_art
    )

    v10_expected = pb2_v10.TaskArtifactUpdateEvent(
        task_id='t1',
        context_id='c1',
        artifact=pb2_v10.Artifact(
            artifact_id='a1', parts=[pb2_v10.Part(text='d')]
        ),
    )

    v10_event = to_core_task_artifact_update_event(v03_event)
    assert v10_event == v10_expected

    v03_restored = to_compat_task_artifact_update_event(v10_event)
    v03_expected_restored = types_v03.TaskArtifactUpdateEvent(
        task_id='t1',
        context_id='c1',
        artifact=v03_art,
        append=False,  # primitive bools default to False
        last_chunk=False,
        metadata=None,
    )
    assert v03_restored == v03_expected_restored


def test_security_requirement_conversion():
    v03_req = {'oauth': ['read', 'write'], 'apikey': []}

    v10_expected = pb2_v10.SecurityRequirement()
    sl_oauth = pb2_v10.StringList()
    sl_oauth.list.extend(['read', 'write'])
    sl_apikey = pb2_v10.StringList()
    v10_expected.schemes['oauth'].CopyFrom(sl_oauth)
    v10_expected.schemes['apikey'].CopyFrom(sl_apikey)

    v10_req = to_core_security_requirement(v03_req)
    assert v10_req == v10_expected

    v03_restored = to_compat_security_requirement(v10_req)
    assert v03_restored == v03_req


def test_oauth_flows_conversion_auth_code():
    v03_flows = types_v03.OAuthFlows(
        authorization_code=types_v03.AuthorizationCodeOAuthFlow(
            authorization_url='http://auth',
            token_url='http://token',  # noqa: S106
            scopes={'a': 'b'},
            refresh_url='ref1',
        )
    )
    v10_expected = pb2_v10.OAuthFlows(
        authorization_code=pb2_v10.AuthorizationCodeOAuthFlow(
            authorization_url='http://auth',
            token_url='http://token',  # noqa: S106
            scopes={'a': 'b'},
            refresh_url='ref1',
        )
    )
    v10_flows = to_core_oauth_flows(v03_flows)
    assert v10_flows == v10_expected
    v03_restored = to_compat_oauth_flows(v10_flows)
    assert v03_restored == v03_flows


def test_oauth_flows_conversion_client_credentials():
    v03_flows = types_v03.OAuthFlows(
        client_credentials=types_v03.ClientCredentialsOAuthFlow(
            token_url='http://token2',  # noqa: S106
            scopes={'c': 'd'},
            refresh_url='ref2',
        )
    )
    v10_expected = pb2_v10.OAuthFlows(
        client_credentials=pb2_v10.ClientCredentialsOAuthFlow(
            token_url='http://token2',  # noqa: S106
            scopes={'c': 'd'},
            refresh_url='ref2',
        )
    )
    v10_flows = to_core_oauth_flows(v03_flows)
    assert v10_flows == v10_expected
    v03_restored = to_compat_oauth_flows(v10_flows)
    assert v03_restored == v03_flows


def test_oauth_flows_conversion_implicit():
    v03_flows = types_v03.OAuthFlows(
        implicit=types_v03.ImplicitOAuthFlow(
            authorization_url='http://auth2',
            scopes={'e': 'f'},
            refresh_url='ref3',
        )
    )
    v10_expected = pb2_v10.OAuthFlows(
        implicit=pb2_v10.ImplicitOAuthFlow(
            authorization_url='http://auth2',
            scopes={'e': 'f'},
            refresh_url='ref3',
        )
    )
    v10_flows = to_core_oauth_flows(v03_flows)
    assert v10_flows == v10_expected
    v03_restored = to_compat_oauth_flows(v10_flows)
    assert v03_restored == v03_flows


def test_oauth_flows_conversion_password():
    v03_flows = types_v03.OAuthFlows(
        password=types_v03.PasswordOAuthFlow(
            token_url='http://token3',  # noqa: S106
            scopes={'g': 'h'},
            refresh_url='ref4',
        )
    )
    v10_expected = pb2_v10.OAuthFlows(
        password=pb2_v10.PasswordOAuthFlow(
            token_url='http://token3',  # noqa: S106
            scopes={'g': 'h'},
            refresh_url='ref4',
        )
    )
    v10_flows = to_core_oauth_flows(v03_flows)
    assert v10_flows == v10_expected
    v03_restored = to_compat_oauth_flows(v10_flows)
    assert v03_restored == v03_flows


def test_security_scheme_apikey():
    v03_scheme = types_v03.SecurityScheme(
        root=types_v03.APIKeySecurityScheme(
            in_=types_v03.In.header, name='X-API-KEY', description='desc'
        )
    )
    v10_expected = pb2_v10.SecurityScheme(
        api_key_security_scheme=pb2_v10.APIKeySecurityScheme(
            location='header', name='X-API-KEY', description='desc'
        )
    )
    v10_scheme = to_core_security_scheme(v03_scheme)
    assert v10_scheme == v10_expected
    v03_restored = to_compat_security_scheme(v10_scheme)
    assert v03_restored == v03_scheme


def test_security_scheme_http_auth():
    v03_scheme = types_v03.SecurityScheme(
        root=types_v03.HTTPAuthSecurityScheme(
            scheme='Bearer', bearer_format='JWT', description='desc'
        )
    )
    v10_expected = pb2_v10.SecurityScheme(
        http_auth_security_scheme=pb2_v10.HTTPAuthSecurityScheme(
            scheme='Bearer', bearer_format='JWT', description='desc'
        )
    )
    v10_scheme = to_core_security_scheme(v03_scheme)
    assert v10_scheme == v10_expected
    v03_restored = to_compat_security_scheme(v10_scheme)
    assert v03_restored == v03_scheme


def test_security_scheme_oauth2():
    v03_flows = types_v03.OAuthFlows(
        authorization_code=types_v03.AuthorizationCodeOAuthFlow(
            authorization_url='u',
            token_url='t',  # noqa: S106
            scopes={},
        )
    )
    v03_scheme = types_v03.SecurityScheme(
        root=types_v03.OAuth2SecurityScheme(
            flows=v03_flows, oauth2_metadata_url='url', description='desc'
        )
    )

    v10_expected = pb2_v10.SecurityScheme(
        oauth2_security_scheme=pb2_v10.OAuth2SecurityScheme(
            flows=pb2_v10.OAuthFlows(
                authorization_code=pb2_v10.AuthorizationCodeOAuthFlow(
                    authorization_url='u',
                    token_url='t',  # noqa: S106
                )
            ),
            oauth2_metadata_url='url',
            description='desc',
        )
    )
    v10_scheme = to_core_security_scheme(v03_scheme)
    assert v10_scheme == v10_expected
    v03_restored = to_compat_security_scheme(v10_scheme)
    assert v03_restored == v03_scheme


def test_security_scheme_oidc():
    v03_scheme = types_v03.SecurityScheme(
        root=types_v03.OpenIdConnectSecurityScheme(
            open_id_connect_url='url', description='desc'
        )
    )
    v10_expected = pb2_v10.SecurityScheme(
        open_id_connect_security_scheme=pb2_v10.OpenIdConnectSecurityScheme(
            open_id_connect_url='url', description='desc'
        )
    )
    v10_scheme = to_core_security_scheme(v03_scheme)
    assert v10_scheme == v10_expected
    v03_restored = to_compat_security_scheme(v10_scheme)
    assert v03_restored == v03_scheme


def test_security_scheme_mtls():
    v03_scheme = types_v03.SecurityScheme(
        root=types_v03.MutualTLSSecurityScheme(description='desc')
    )
    v10_expected = pb2_v10.SecurityScheme(
        mtls_security_scheme=pb2_v10.MutualTlsSecurityScheme(description='desc')
    )
    v10_scheme = to_core_security_scheme(v03_scheme)
    assert v10_scheme == v10_expected
    v03_restored = to_compat_security_scheme(v10_scheme)
    assert v03_restored == v03_scheme


def test_oauth_flows_conversion_minimal():
    v03_flows = types_v03.OAuthFlows(
        authorization_code=types_v03.AuthorizationCodeOAuthFlow(
            authorization_url='http://auth',
            token_url='http://token',  # noqa: S106
            scopes={'a': 'b'},
        )  # no refresh_url
    )
    v10_expected = pb2_v10.OAuthFlows(
        authorization_code=pb2_v10.AuthorizationCodeOAuthFlow(
            authorization_url='http://auth',
            token_url='http://token',  # noqa: S106
            scopes={'a': 'b'},
        )
    )
    v10_flows = to_core_oauth_flows(v03_flows)
    assert v10_flows == v10_expected

    v03_restored = to_compat_oauth_flows(v10_flows)
    assert v03_restored == v03_flows


def test_security_scheme_minimal():
    v03_scheme = types_v03.SecurityScheme(
        root=types_v03.APIKeySecurityScheme(
            in_=types_v03.In.header,
            name='X-API-KEY',  # no description
        )
    )
    v10_expected = pb2_v10.SecurityScheme(
        api_key_security_scheme=pb2_v10.APIKeySecurityScheme(
            location='header', name='X-API-KEY'
        )
    )
    v10_scheme = to_core_security_scheme(v03_scheme)
    assert v10_scheme == v10_expected
    v03_restored = to_compat_security_scheme(v10_scheme)
    assert v03_restored == v03_scheme


def test_security_scheme_http_auth_minimal():
    v03_scheme = types_v03.SecurityScheme(
        root=types_v03.HTTPAuthSecurityScheme(
            scheme='Bearer'  # no bearer_format, no description
        )
    )
    v10_expected = pb2_v10.SecurityScheme(
        http_auth_security_scheme=pb2_v10.HTTPAuthSecurityScheme(
            scheme='Bearer'
        )
    )
    v10_scheme = to_core_security_scheme(v03_scheme)
    assert v10_scheme == v10_expected
    v03_restored = to_compat_security_scheme(v10_scheme)
    assert v03_restored == v03_scheme


def test_security_scheme_oauth2_minimal():
    v03_flows = types_v03.OAuthFlows(
        implicit=types_v03.ImplicitOAuthFlow(authorization_url='u', scopes={})
    )
    v03_scheme = types_v03.SecurityScheme(
        root=types_v03.OAuth2SecurityScheme(
            flows=v03_flows  # no oauth2_metadata_url, no description
        )
    )
    v10_expected = pb2_v10.SecurityScheme(
        oauth2_security_scheme=pb2_v10.OAuth2SecurityScheme(
            flows=pb2_v10.OAuthFlows(
                implicit=pb2_v10.ImplicitOAuthFlow(authorization_url='u')
            )
        )
    )
    v10_scheme = to_core_security_scheme(v03_scheme)
    assert v10_scheme == v10_expected
    v03_restored = to_compat_security_scheme(v10_scheme)
    assert v03_restored == v03_scheme


def test_security_scheme_oidc_minimal():
    v03_scheme = types_v03.SecurityScheme(
        root=types_v03.OpenIdConnectSecurityScheme(
            open_id_connect_url='url'  # no description
        )
    )
    v10_expected = pb2_v10.SecurityScheme(
        open_id_connect_security_scheme=pb2_v10.OpenIdConnectSecurityScheme(
            open_id_connect_url='url'
        )
    )
    v10_scheme = to_core_security_scheme(v03_scheme)
    assert v10_scheme == v10_expected
    v03_restored = to_compat_security_scheme(v10_scheme)
    assert v03_restored == v03_scheme


def test_security_scheme_mtls_minimal():
    v03_scheme = types_v03.SecurityScheme(
        root=types_v03.MutualTLSSecurityScheme()
    )
    v10_expected = pb2_v10.SecurityScheme(
        mtls_security_scheme=pb2_v10.MutualTlsSecurityScheme()
    )
    v10_scheme = to_core_security_scheme(v03_scheme)
    assert v10_scheme == v10_expected
    v03_restored = to_compat_security_scheme(v10_scheme)
    assert v03_restored == v03_scheme
    v10_scheme = pb2_v10.SecurityScheme()
    with pytest.raises(ValueError, match='Unknown security scheme type'):
        to_compat_security_scheme(v10_scheme)


def test_agent_interface_conversion():
    v03_int = types_v03.AgentInterface(url='http', transport='JSONRPC')
    v10_expected = pb2_v10.AgentInterface(
        url='http', protocol_binding='JSONRPC', protocol_version='0.3'
    )
    v10_int = to_core_agent_interface(v03_int)
    assert v10_int == v10_expected
    v03_restored = to_compat_agent_interface(v10_int)
    assert v03_restored == v03_int


def test_agent_provider_conversion():
    v03_prov = types_v03.AgentProvider(url='u', organization='org')
    v10_expected = pb2_v10.AgentProvider(url='u', organization='org')
    v10_prov = to_core_agent_provider(v03_prov)
    assert v10_prov == v10_expected
    v03_restored = to_compat_agent_provider(v10_prov)
    assert v03_restored == v03_prov


def test_agent_extension_conversion():
    v03_ext = types_v03.AgentExtension(
        uri='u', description='d', required=True, params={'k': 'v'}
    )
    v10_expected = pb2_v10.AgentExtension(
        uri='u', description='d', required=True
    )
    ParseDict({'k': 'v'}, v10_expected.params)
    v10_ext = to_core_agent_extension(v03_ext)
    assert v10_ext == v10_expected
    v03_restored = to_compat_agent_extension(v10_ext)
    assert v03_restored == v03_ext


def test_agent_capabilities_conversion():
    v03_ext = types_v03.AgentExtension(uri='u', required=False)
    v03_cap = types_v03.AgentCapabilities(
        streaming=True,
        push_notifications=False,
        extensions=[v03_ext],
        state_transition_history=True,
    )
    v10_expected = pb2_v10.AgentCapabilities(
        streaming=True,
        push_notifications=False,
        extensions=[pb2_v10.AgentExtension(uri='u', required=False)],
    )
    v10_cap = to_core_agent_capabilities(v03_cap)
    assert v10_cap == v10_expected
    v03_restored = to_compat_agent_capabilities(v10_cap)
    v03_expected_restored = types_v03.AgentCapabilities(
        streaming=True,
        push_notifications=False,
        extensions=[v03_ext],
        state_transition_history=None,
    )
    assert v03_restored == v03_expected_restored


def test_agent_skill_conversion():
    v03_skill = types_v03.AgentSkill(
        id='s1',
        name='n',
        description='d',
        tags=['t'],
        examples=['e'],
        input_modes=['i'],
        output_modes=['o'],
        security=[{'s': ['1']}],
    )
    v10_expected = pb2_v10.AgentSkill(
        id='s1',
        name='n',
        description='d',
        tags=['t'],
        examples=['e'],
        input_modes=['i'],
        output_modes=['o'],
    )
    sl = pb2_v10.StringList()
    sl.list.extend(['1'])
    v10_expected.security_requirements.add().schemes['s'].CopyFrom(sl)

    v10_skill = to_core_agent_skill(v03_skill)
    assert v10_skill == v10_expected
    v03_restored = to_compat_agent_skill(v10_skill)
    assert v03_restored == v03_skill


def test_agent_card_signature_conversion():
    v03_sig = types_v03.AgentCardSignature(
        protected='p', signature='s', header={'h': 'v'}
    )
    v10_expected = pb2_v10.AgentCardSignature(protected='p', signature='s')
    ParseDict({'h': 'v'}, v10_expected.header)
    v10_sig = to_core_agent_card_signature(v03_sig)
    assert v10_sig == v10_expected
    v03_restored = to_compat_agent_card_signature(v10_sig)
    assert v03_restored == v03_sig


def test_agent_card_conversion():
    v03_int = types_v03.AgentInterface(url='u2', transport='HTTP')
    v03_cap = types_v03.AgentCapabilities(streaming=True)
    v03_skill = types_v03.AgentSkill(
        id='s1',
        name='sn',
        description='sd',
        tags=[],
        input_modes=[],
        output_modes=[],
    )
    v03_prov = types_v03.AgentProvider(url='pu', organization='po')

    v03_card = types_v03.AgentCard(
        name='n',
        description='d',
        version='v',
        url='u1',
        preferred_transport='JSONRPC',
        protocol_version='0.3.0',
        additional_interfaces=[v03_int],
        provider=v03_prov,
        documentation_url='du',
        icon_url='iu',
        capabilities=v03_cap,
        supports_authenticated_extended_card=True,
        security=[{'s': []}],
        default_input_modes=['i'],
        default_output_modes=['o'],
        skills=[v03_skill],
    )

    v10_expected = pb2_v10.AgentCard(
        name='n',
        description='d',
        version='v',
        documentation_url='du',
        icon_url='iu',
        default_input_modes=['i'],
        default_output_modes=['o'],
    )
    v10_expected.supported_interfaces.extend(
        [
            pb2_v10.AgentInterface(
                url='u1', protocol_binding='JSONRPC', protocol_version='0.3.0'
            ),
            pb2_v10.AgentInterface(
                url='u2', protocol_binding='HTTP', protocol_version='0.3'
            ),
        ]
    )
    v10_expected.provider.CopyFrom(
        pb2_v10.AgentProvider(url='pu', organization='po')
    )
    v10_expected.capabilities.CopyFrom(
        pb2_v10.AgentCapabilities(streaming=True, extended_agent_card=True)
    )
    v10_expected.security_requirements.add().schemes['s'].CopyFrom(
        pb2_v10.StringList()
    )
    v10_expected.skills.add().CopyFrom(
        pb2_v10.AgentSkill(id='s1', name='sn', description='sd')
    )

    v10_card = to_core_agent_card(v03_card)
    assert v10_card == v10_expected

    v03_restored = to_compat_agent_card(v10_card)
    # We must explicitly set capabilities.state_transition_history to None in our original to match the restored
    v03_card.capabilities.state_transition_history = None
    # AgentSkill empty lists are converted to None during restoration
    v03_card.skills[0].input_modes = None
    v03_card.skills[0].output_modes = None
    v03_card.skills[0].security = None
    v03_card.skills[0].examples = None
    assert v03_restored == v03_card


def test_agent_card_conversion_minimal():
    v03_cap = types_v03.AgentCapabilities()
    v03_card = types_v03.AgentCard(
        name='n',
        description='d',
        version='v',
        url='u1',
        preferred_transport='JSONRPC',
        protocol_version='0.3.0',
        capabilities=v03_cap,
        default_input_modes=[],
        default_output_modes=[],
        skills=[],
    )
    v10_expected = pb2_v10.AgentCard(
        name='n',
        description='d',
        version='v',
        capabilities=pb2_v10.AgentCapabilities(),
    )
    v10_expected.supported_interfaces.extend(
        [
            pb2_v10.AgentInterface(
                url='u1', protocol_binding='JSONRPC', protocol_version='0.3.0'
            )
        ]
    )
    v10_card = to_core_agent_card(v03_card)
    assert v10_card == v10_expected

    v03_restored = to_compat_agent_card(v10_card)
    v03_card.capabilities.state_transition_history = None
    assert v03_restored == v03_card


def test_agent_skill_conversion_minimal():
    v03_skill = types_v03.AgentSkill(
        id='s1',
        name='n',
        description='d',
        tags=[],
        input_modes=[],
        output_modes=[],
    )
    v10_expected = pb2_v10.AgentSkill(id='s1', name='n', description='d')
    v10_skill = to_core_agent_skill(v03_skill)
    assert v10_skill == v10_expected
    v03_restored = to_compat_agent_skill(v10_skill)

    # Restore sets missing optional lists to None usually. We adjust expected here
    v03_expected_restored = types_v03.AgentSkill(
        id='s1',
        name='n',
        description='d',
        tags=[],
        examples=None,
        input_modes=None,
        output_modes=None,
        security=None,
    )
    assert v03_restored == v03_expected_restored


def test_agent_extension_conversion_minimal():
    v03_ext = types_v03.AgentExtension(uri='u', required=False)
    v10_expected = pb2_v10.AgentExtension(uri='u', required=False)
    v10_ext = to_core_agent_extension(v03_ext)
    assert v10_ext == v10_expected
    v03_restored = to_compat_agent_extension(v10_ext)
    v03_expected_restored = types_v03.AgentExtension(
        uri='u', description=None, required=False, params=None
    )
    assert v03_restored == v03_expected_restored


def test_task_push_notification_config_conversion():
    v03_auth = types_v03.PushNotificationAuthenticationInfo(schemes=['Basic'])
    v03_cfg = types_v03.TaskPushNotificationConfig(
        task_id='t1',
        push_notification_config=types_v03.PushNotificationConfig(
            id='c1',
            url='http://url',
            token='tok',  # noqa: S106
            authentication=v03_auth,
        ),
    )
    v10_expected = pb2_v10.TaskPushNotificationConfig(
        task_id='t1',
        id='c1',
        url='http://url',
        token='tok',  # noqa: S106
        authentication=pb2_v10.AuthenticationInfo(scheme='Basic'),
    )
    v10_cfg = to_core_task_push_notification_config(v03_cfg)
    assert v10_cfg == v10_expected
    v03_restored = to_compat_task_push_notification_config(v10_cfg)

    v03_expected_restored = types_v03.TaskPushNotificationConfig(
        task_id='t1',
        push_notification_config=types_v03.PushNotificationConfig(
            id='c1',
            url='http://url',
            token='tok',  # noqa: S106
            authentication=v03_auth,
        ),
    )
    assert v03_restored == v03_expected_restored


def test_task_push_notification_config_conversion_minimal():
    v03_cfg = types_v03.TaskPushNotificationConfig(
        task_id='t1',
        push_notification_config=types_v03.PushNotificationConfig(
            url='http://url'
        ),
    )
    v10_expected = pb2_v10.TaskPushNotificationConfig(
        task_id='t1', url='http://url'
    )
    v10_cfg = to_core_task_push_notification_config(v03_cfg)
    assert v10_cfg == v10_expected
    v03_restored = to_compat_task_push_notification_config(v10_cfg)
    v03_expected_restored = types_v03.TaskPushNotificationConfig(
        task_id='t1',
        push_notification_config=types_v03.PushNotificationConfig(
            url='http://url'
        ),
    )
    assert v03_restored == v03_expected_restored


def test_send_message_request_conversion():
    v03_msg = types_v03.Message(
        message_id='m1',
        role=types_v03.Role.user,
        parts=[types_v03.Part(root=types_v03.TextPart(text='Hi'))],
    )
    v03_cfg = types_v03.MessageSendConfiguration(history_length=5)
    v03_req = types_v03.SendMessageRequest(
        id='conv',
        params=types_v03.MessageSendParams(
            message=v03_msg, configuration=v03_cfg, metadata={'k': 'v'}
        ),
    )
    v10_expected = pb2_v10.SendMessageRequest(
        message=pb2_v10.Message(
            message_id='m1',
            role=pb2_v10.Role.ROLE_USER,
            parts=[pb2_v10.Part(text='Hi')],
        ),
        configuration=pb2_v10.SendMessageConfiguration(history_length=5),
    )
    ParseDict({'k': 'v'}, v10_expected.metadata)

    v10_req = to_core_send_message_request(v03_req)
    assert v10_req == v10_expected
    v03_restored = to_compat_send_message_request(v10_req, request_id='conv')
    assert v03_restored.id == 'conv'
    assert v03_restored.params.message.message_id == 'm1'
    assert v03_restored.params.configuration.history_length == 5
    assert v03_restored.params.metadata == {'k': 'v'}


def test_get_task_request_conversion():
    v03_req = types_v03.GetTaskRequest(
        id='conv', params=types_v03.TaskQueryParams(id='t1', history_length=10)
    )
    v10_expected = pb2_v10.GetTaskRequest(id='t1', history_length=10)
    v10_req = to_core_get_task_request(v03_req)
    assert v10_req == v10_expected
    v03_restored = to_compat_get_task_request(v10_req, request_id='conv')
    assert v03_restored == v03_req


def test_get_task_request_conversion_minimal():
    v03_req = types_v03.GetTaskRequest(
        id='conv', params=types_v03.TaskQueryParams(id='t1')
    )
    v10_expected = pb2_v10.GetTaskRequest(id='t1')
    v10_req = to_core_get_task_request(v03_req)
    assert v10_req == v10_expected
    v03_restored = to_compat_get_task_request(v10_req, request_id='conv')
    assert v03_restored == v03_req


def test_cancel_task_request_conversion():
    v03_req = types_v03.CancelTaskRequest(
        id='conv',
        params=types_v03.TaskIdParams(id='t1', metadata={'reason': 'test'}),
    )
    v10_expected = pb2_v10.CancelTaskRequest(id='t1')
    ParseDict({'reason': 'test'}, v10_expected.metadata)
    v10_req = to_core_cancel_task_request(v03_req)
    assert v10_req == v10_expected
    v03_restored = to_compat_cancel_task_request(v10_req, request_id='conv')
    assert v03_restored == v03_req


def test_cancel_task_request_conversion_minimal():
    v03_req = types_v03.CancelTaskRequest(
        id='conv', params=types_v03.TaskIdParams(id='t1')
    )
    v10_expected = pb2_v10.CancelTaskRequest(id='t1')
    v10_req = to_core_cancel_task_request(v03_req)
    assert v10_req == v10_expected
    v03_restored = to_compat_cancel_task_request(v10_req, request_id='conv')
    assert v03_restored == v03_req


def test_create_task_push_notification_config_request_conversion():
    v03_cfg = types_v03.TaskPushNotificationConfig(
        task_id='t1',
        push_notification_config=types_v03.PushNotificationConfig(url='u'),
    )
    v03_req = types_v03.SetTaskPushNotificationConfigRequest(
        id='conv', params=v03_cfg
    )
    v10_expected = pb2_v10.TaskPushNotificationConfig(task_id='t1', url='u')
    v10_req = to_core_create_task_push_notification_config_request(v03_req)
    assert v10_req == v10_expected
    v03_restored = to_compat_create_task_push_notification_config_request(
        v10_req, request_id='conv'
    )
    assert v03_restored == v03_req


def test_stream_response_conversion():
    v03_msg = types_v03.Message(
        message_id='m1',
        role=types_v03.Role.user,
        parts=[types_v03.Part(root=types_v03.TextPart(text='Hi'))],
    )
    v03_res = types_v03.SendStreamingMessageSuccessResponse(result=v03_msg)
    v10_expected = pb2_v10.StreamResponse(
        message=pb2_v10.Message(
            message_id='m1',
            role=pb2_v10.Role.ROLE_USER,
            parts=[pb2_v10.Part(text='Hi')],
        )
    )
    v10_res = to_core_stream_response(v03_res)
    assert v10_res == v10_expected


def test_get_task_push_notification_config_request_conversion():
    v03_req = types_v03.GetTaskPushNotificationConfigRequest(
        id='conv', params=types_v03.TaskIdParams(id='t1')
    )
    v10_expected = pb2_v10.GetTaskPushNotificationConfigRequest(task_id='t1')
    v10_req = to_core_get_task_push_notification_config_request(v03_req)
    assert v10_req == v10_expected
    v03_restored = to_compat_get_task_push_notification_config_request(
        v10_req, request_id='conv'
    )
    assert v03_restored == v03_req


def test_delete_task_push_notification_config_request_conversion():
    v03_req = types_v03.DeleteTaskPushNotificationConfigRequest(
        id='conv',
        params=types_v03.DeleteTaskPushNotificationConfigParams(
            id='t1', push_notification_config_id='p1'
        ),
    )
    v10_expected = pb2_v10.DeleteTaskPushNotificationConfigRequest(
        task_id='t1', id='p1'
    )
    v10_req = to_core_delete_task_push_notification_config_request(v03_req)
    assert v10_req == v10_expected
    v03_restored = to_compat_delete_task_push_notification_config_request(
        v10_req, request_id='conv'
    )
    assert v03_restored == v03_req


def test_subscribe_to_task_request_conversion():
    v03_req = types_v03.TaskResubscriptionRequest(
        id='conv', params=types_v03.TaskIdParams(id='t1')
    )
    v10_expected = pb2_v10.SubscribeToTaskRequest(id='t1')
    v10_req = to_core_subscribe_to_task_request(v03_req)
    assert v10_req == v10_expected
    v03_restored = to_compat_subscribe_to_task_request(
        v10_req, request_id='conv'
    )
    assert v03_restored == v03_req


def test_list_task_push_notification_config_request_conversion():
    v03_req = types_v03.ListTaskPushNotificationConfigRequest(
        id='conv',
        params=types_v03.ListTaskPushNotificationConfigParams(id='t1'),
    )
    v10_expected = pb2_v10.ListTaskPushNotificationConfigsRequest(task_id='t1')
    v10_req = to_core_list_task_push_notification_config_request(v03_req)
    assert v10_req == v10_expected
    v03_restored = to_compat_list_task_push_notification_config_request(
        v10_req, request_id='conv'
    )
    assert v03_restored == v03_req


def test_list_task_push_notification_config_response_conversion():
    v03_cfg = types_v03.TaskPushNotificationConfig(
        task_id='t1',
        push_notification_config=types_v03.PushNotificationConfig(url='u'),
    )
    v03_res = types_v03.ListTaskPushNotificationConfigResponse(
        root=types_v03.ListTaskPushNotificationConfigSuccessResponse(
            id='conv', result=[v03_cfg]
        )
    )
    v10_expected = pb2_v10.ListTaskPushNotificationConfigsResponse(
        configs=[pb2_v10.TaskPushNotificationConfig(task_id='t1', url='u')]
    )
    v10_res = to_core_list_task_push_notification_config_response(v03_res)
    assert v10_res == v10_expected
    v03_restored = to_compat_list_task_push_notification_config_response(
        v10_res, request_id='conv'
    )
    assert v03_restored == v03_res


def test_send_message_response_conversion():
    v03_task = types_v03.Task(
        id='t1',
        context_id='c1',
        status=types_v03.TaskStatus(state=types_v03.TaskState.unknown),
    )
    v03_res = types_v03.SendMessageResponse(
        root=types_v03.SendMessageSuccessResponse(id='conv', result=v03_task)
    )
    v10_expected = pb2_v10.SendMessageResponse(
        task=pb2_v10.Task(
            id='t1',
            context_id='c1',
            status=pb2_v10.TaskStatus(
                state=pb2_v10.TaskState.TASK_STATE_UNSPECIFIED
            ),
        )
    )
    v10_res = to_core_send_message_response(v03_res)
    assert v10_res == v10_expected
    v03_restored = to_compat_send_message_response(v10_res, request_id='conv')
    assert v03_restored == v03_res


def test_stream_response_conversion_with_id():
    v10_res = pb2_v10.StreamResponse(
        message=pb2_v10.Message(
            message_id='m1',
            role=pb2_v10.Role.ROLE_USER,
            parts=[pb2_v10.Part(text='Hi')],
        )
    )
    v03_res = to_compat_stream_response(v10_res, request_id='req123')
    assert v03_res.id == 'req123'
    assert v03_res.result.message_id == 'm1'


def test_get_extended_agent_card_request_conversion():
    v03_req = types_v03.GetAuthenticatedExtendedCardRequest(id='conv')
    v10_expected = pb2_v10.GetExtendedAgentCardRequest()
    v10_req = to_core_get_extended_agent_card_request(v03_req)
    assert v10_req == v10_expected
    v03_restored = to_compat_get_extended_agent_card_request(
        v10_req, request_id='conv'
    )
    assert v03_restored == v03_req


def test_get_task_push_notification_config_request_conversion_full_params():
    v03_req = types_v03.GetTaskPushNotificationConfigRequest(
        id='conv',
        params=types_v03.GetTaskPushNotificationConfigParams(
            id='t1', push_notification_config_id='p1'
        ),
    )
    v10_expected = pb2_v10.GetTaskPushNotificationConfigRequest(
        task_id='t1', id='p1'
    )
    v10_req = to_core_get_task_push_notification_config_request(v03_req)
    assert v10_req == v10_expected
    v03_restored = to_compat_get_task_push_notification_config_request(
        v10_req, request_id='conv'
    )
    assert v03_restored == v03_req


def test_send_message_response_conversion_message():
    v03_msg = types_v03.Message(
        message_id='m1',
        role=types_v03.Role.agent,
        parts=[types_v03.Part(root=types_v03.TextPart(text='Hi'))],
    )
    v03_res = types_v03.SendMessageResponse(
        root=types_v03.SendMessageSuccessResponse(id='conv', result=v03_msg)
    )
    v10_expected = pb2_v10.SendMessageResponse(
        message=pb2_v10.Message(
            message_id='m1',
            role=pb2_v10.Role.ROLE_AGENT,
            parts=[pb2_v10.Part(text='Hi')],
        )
    )
    v10_res = to_core_send_message_response(v03_res)
    assert v10_res == v10_expected
    v03_restored = to_compat_send_message_response(v10_res, request_id='conv')
    assert v03_restored == v03_res


def test_stream_response_conversion_status_update():
    v03_status_event = types_v03.TaskStatusUpdateEvent(
        task_id='t1',
        context_id='c1',
        status=types_v03.TaskStatus(state=types_v03.TaskState.working),
        final=False,
    )
    v03_res = types_v03.SendStreamingMessageSuccessResponse(
        id='conv', result=v03_status_event
    )
    v10_expected = pb2_v10.StreamResponse(
        status_update=pb2_v10.TaskStatusUpdateEvent(
            task_id='t1',
            context_id='c1',
            status=pb2_v10.TaskStatus(
                state=pb2_v10.TaskState.TASK_STATE_WORKING
            ),
        )
    )
    v10_res = to_core_stream_response(v03_res)
    assert v10_res == v10_expected
    v03_restored = to_compat_stream_response(v10_res, request_id='conv')
    assert v03_restored == v03_res


def test_stream_response_conversion_artifact_update():
    v03_art = types_v03.Artifact(
        artifact_id='a1',
        parts=[types_v03.Part(root=types_v03.TextPart(text='d'))],
    )
    v03_artifact_event = types_v03.TaskArtifactUpdateEvent(
        task_id='t1', context_id='c1', artifact=v03_art
    )
    v03_res = types_v03.SendStreamingMessageSuccessResponse(
        id='conv', result=v03_artifact_event
    )
    v10_expected = pb2_v10.StreamResponse(
        artifact_update=pb2_v10.TaskArtifactUpdateEvent(
            task_id='t1',
            context_id='c1',
            artifact=pb2_v10.Artifact(
                artifact_id='a1', parts=[pb2_v10.Part(text='d')]
            ),
        )
    )
    v10_res = to_core_stream_response(v03_res)
    assert v10_res == v10_expected
    v03_restored = to_compat_stream_response(v10_res, request_id='conv')
    # restored artifact update has default append=False, last_chunk=False
    v03_expected = types_v03.SendStreamingMessageSuccessResponse(
        id='conv',
        result=types_v03.TaskArtifactUpdateEvent(
            task_id='t1',
            context_id='c1',
            artifact=v03_art,
            append=False,
            last_chunk=False,
        ),
    )
    assert v03_restored == v03_expected


def test_oauth_flows_conversion_priority():
    # v03 allows multiple, v10 allows one (oneof)
    v03_flows = types_v03.OAuthFlows(
        authorization_code=types_v03.AuthorizationCodeOAuthFlow(
            authorization_url='http://auth',
            token_url='http://token',  # noqa: S106
            scopes={'a': 'b'},
        ),
        client_credentials=types_v03.ClientCredentialsOAuthFlow(
            token_url='http://token2',  # noqa: S106
            scopes={'c': 'd'},
        ),
    )

    core_flows = to_core_oauth_flows(v03_flows)
    # The last one set wins in proto oneof. In conversions.py order is:
    # authorization_code, client_credentials, implicit, password.
    # So client_credentials should win over authorization_code.
    assert core_flows.WhichOneof('flow') == 'client_credentials'
    assert core_flows.client_credentials.token_url == 'http://token2'  # noqa: S105


def test_to_core_part_data_part_with_metadata_not_compat():
    v03_part = types_v03.Part(
        root=types_v03.DataPart(
            data={'foo': 'bar'}, metadata={'other_key': 'val'}
        )
    )
    core_part = to_core_part(v03_part)
    assert core_part.data.struct_value['foo'] == 'bar'
    assert core_part.metadata['other_key'] == 'val'


def test_to_core_part_file_with_bytes_minimal():
    v03_part = types_v03.Part(
        root=types_v03.FilePart(
            file=types_v03.FileWithBytes(bytes='YmFzZTY0')
            # missing mime_type and name
        )
    )
    core_part = to_core_part(v03_part)
    assert core_part.raw == b'base64'
    assert not core_part.media_type
    assert not core_part.filename


def test_to_core_part_file_with_uri_minimal():
    v03_part = types_v03.Part(
        root=types_v03.FilePart(
            file=types_v03.FileWithUri(uri='http://test')
            # missing mime_type and name
        )
    )
    core_part = to_core_part(v03_part)
    assert core_part.url == 'http://test'
    assert not core_part.media_type
    assert not core_part.filename


def test_to_compat_part_unknown_content():
    core_part = pb2_v10.Part()
    # It has no content set (WhichOneof returns None)
    with pytest.raises(ValueError, match='Unknown part content type: None'):
        to_compat_part(core_part)


def test_to_core_message_unspecified_role():
    v03_msg = types_v03.Message(
        message_id='m1',
        role=types_v03.Role.user,  # Required by pydantic model, bypass to None for test
        parts=[],
    )
    v03_msg.role = None
    core_msg = to_core_message(v03_msg)
    assert core_msg.role == pb2_v10.Role.ROLE_UNSPECIFIED


def test_to_core_task_status_missing_state():
    v03_status = types_v03.TaskStatus.model_construct(state=None)
    core_status = to_core_task_status(v03_status)
    assert core_status.state == pb2_v10.TaskState.TASK_STATE_UNSPECIFIED


def test_to_core_task_status_update_event_missing_status():
    v03_event = types_v03.TaskStatusUpdateEvent.model_construct(
        task_id='t1', context_id='c1', status=None, final=False
    )
    core_event = to_core_task_status_update_event(v03_event)
    assert not core_event.HasField('status')


def test_to_core_task_artifact_update_event_missing_artifact():
    v03_event = types_v03.TaskArtifactUpdateEvent.model_construct(
        task_id='t1', context_id='c1', artifact=None
    )
    core_event = to_core_task_artifact_update_event(v03_event)
    assert not core_event.HasField('artifact')


def test_to_core_agent_card_with_security_and_signatures():
    v03_card = types_v03.AgentCard.model_construct(
        name='test',
        description='test',
        version='1.0',
        url='http://url',
        capabilities=types_v03.AgentCapabilities(),
        security_schemes={
            'scheme1': types_v03.SecurityScheme(
                root=types_v03.MutualTLSSecurityScheme.model_construct(
                    description='mtls'
                )
            )
        },
        signatures=[
            types_v03.AgentCardSignature.model_construct(
                protected='prot', signature='sig'
            )
        ],
        default_input_modes=[],
        default_output_modes=[],
        skills=[],
    )
    core_card = to_core_agent_card(v03_card)
    assert 'scheme1' in core_card.security_schemes
    assert len(core_card.signatures) == 1
    assert core_card.signatures[0].signature == 'sig'


def test_to_core_send_message_request_no_configuration():
    v03_req = types_v03.SendMessageRequest.model_construct(
        id=1,
        params=types_v03.MessageSendParams.model_construct(
            message=None, configuration=None, metadata=None
        ),
    )
    core_req = to_core_send_message_request(v03_req)
    # Blocking by default (return_immediately=False)
    assert core_req.configuration.return_immediately is False
    assert not core_req.HasField('message')


def test_to_core_list_task_push_notification_config_response_error():
    v03_res = types_v03.ListTaskPushNotificationConfigResponse(
        root=types_v03.JSONRPCErrorResponse(
            id=1, error=types_v03.JSONRPCError(code=-32000, message='Error')
        )
    )
    core_res = to_core_list_task_push_notification_config_response(v03_res)
    assert len(core_res.configs) == 0


def test_to_core_send_message_response_error():
    v03_res = types_v03.SendMessageResponse(
        root=types_v03.JSONRPCErrorResponse(
            id=1, error=types_v03.JSONRPCError(code=-32000, message='Error')
        )
    )
    core_res = to_core_send_message_response(v03_res)
    assert not core_res.HasField('message')
    assert not core_res.HasField('task')


def test_stream_response_task_variant():
    v03_task = types_v03.Task(
        id='t1',
        context_id='c1',
        status=types_v03.TaskStatus(state=types_v03.TaskState.working),
    )
    v03_res = types_v03.SendStreamingMessageSuccessResponse(
        id=1, result=v03_task
    )
    core_res = to_core_stream_response(v03_res)
    assert core_res.HasField('task')
    assert core_res.task.id == 't1'

    v03_restored = to_compat_stream_response(core_res, request_id=1)
    assert isinstance(v03_restored.result, types_v03.Task)
    assert v03_restored.result.id == 't1'


def test_to_compat_stream_response_unknown():
    core_res = pb2_v10.StreamResponse()
    with pytest.raises(
        ValueError, match='Unknown stream response event type: None'
    ):
        to_compat_stream_response(core_res)


def test_to_core_part_file_part_with_metadata():
    v03_part = types_v03.Part(
        root=types_v03.FilePart(
            file=types_v03.FileWithBytes(
                bytes='YmFzZTY0', mime_type='test/test', name='test.txt'
            ),
            metadata={'test': 'val'},
        )
    )
    core_part = to_core_part(v03_part)
    assert core_part.metadata['test'] == 'val'


def test_to_core_part_file_part_invalid_file_type():
    v03_part = types_v03.Part.model_construct(
        root=types_v03.FilePart.model_construct(
            file=None,  # Not FileWithBytes or FileWithUri
            metadata=None,
        )
    )
    core_part = to_core_part(v03_part)
    # Should fall through to the end and return an empty part
    assert not core_part.HasField('raw')


def test_to_core_task_missing_status():
    v03_task = types_v03.Task.model_construct(
        id='t1', context_id='c1', status=None
    )
    core_task = to_core_task(v03_task)
    assert not core_task.HasField('status')


def test_to_core_security_scheme_unknown_type():
    v03_scheme = types_v03.SecurityScheme.model_construct(root=None)
    core_scheme = to_core_security_scheme(v03_scheme)
    # Returns an empty SecurityScheme
    assert core_scheme.WhichOneof('scheme') is None


def test_to_core_agent_extension_minimal():
    v03_ext = types_v03.AgentExtension.model_construct(
        uri='', description=None, required=None, params=None
    )
    core_ext = to_core_agent_extension(v03_ext)
    assert core_ext.uri == ''


def test_to_core_task_push_notification_config_missing_config():
    v03_config = types_v03.TaskPushNotificationConfig.model_construct(
        task_id='t1', push_notification_config=None
    )
    core_config = to_core_task_push_notification_config(v03_config)
    assert not core_config.url


def test_to_core_create_task_push_notification_config_request_missing_config():
    v03_req = types_v03.SetTaskPushNotificationConfigRequest.model_construct(
        id=1,
        params=types_v03.TaskPushNotificationConfig.model_construct(
            task_id='t1', push_notification_config=None
        ),
    )
    core_req = to_core_create_task_push_notification_config_request(v03_req)
    assert not core_req.url


def test_to_core_list_task_push_notification_config_request_missing_id():
    v03_req = types_v03.ListTaskPushNotificationConfigRequest.model_construct(
        id=1,
        params=types_v03.ListTaskPushNotificationConfigParams.model_construct(
            id=''
        ),
    )
    core_req = to_core_list_task_push_notification_config_request(v03_req)
    assert core_req.task_id == ''


def test_to_core_stream_response_unknown_result():
    v03_res = types_v03.SendStreamingMessageSuccessResponse.model_construct(
        id=1, result=None
    )
    core_res = to_core_stream_response(v03_res)
    assert core_res.WhichOneof('payload') is None


def test_to_core_part_unknown_part():
    # If the root of the part is somehow none of TextPart, DataPart, or FilePart,
    # it should just return an empty core Part.
    v03_part = types_v03.Part.model_construct(root=None)
    core_part = to_core_part(v03_part)
    assert not core_part.HasField('text')
    assert not core_part.HasField('data')
    assert not core_part.HasField('raw')
    assert not core_part.HasField('url')


def test_task_db_conversion():
    v10_task = pb2_v10.Task(
        id='task-123',
        context_id='ctx-456',
        status=pb2_v10.TaskStatus(
            state=pb2_v10.TaskState.TASK_STATE_WORKING,
        ),
        metadata={'m1': 'v1'},
    )
    owner = 'owner-789'

    # Test Core -> Model
    model = core_to_compat_task_model(v10_task, owner)
    assert model.id == 'task-123'
    assert model.context_id == 'ctx-456'
    assert model.owner == owner
    assert model.protocol_version == '0.3'
    assert model.status['state'] == 'working'
    assert model.task_metadata == {'m1': 'v1'}

    # Test Model -> Core
    v10_restored = compat_task_model_to_core(model)
    assert v10_restored.id == v10_task.id
    assert v10_restored.context_id == v10_task.context_id
    assert v10_restored.status.state == v10_task.status.state
    assert v10_restored.metadata == v10_task.metadata


def test_push_notification_config_db_conversion():
    task_id = 'task-123'
    v10_config = pb2_v10.TaskPushNotificationConfig(
        id='pnc-1',
        url='https://example.com/push',
        token='secret-token',
    )
    owner = 'owner-789'

    # Test Core -> Model (No encryption)
    model = core_to_compat_push_notification_config_model(
        task_id, v10_config, owner
    )
    assert model.task_id == task_id
    assert model.config_id == 'pnc-1'
    assert model.owner == owner
    assert model.protocol_version == '0.3'

    import json

    data = json.loads(model.config_data.decode('utf-8'))
    assert data['url'] == 'https://example.com/push'
    assert data['token'] == 'secret-token'

    # Test Model -> Core
    v10_restored = compat_push_notification_config_model_to_core(
        model.config_data.decode('utf-8'), task_id
    )
    assert v10_restored.id == v10_config.id
    assert v10_restored.url == v10_config.url
    assert v10_restored.token == v10_config.token


def test_push_notification_config_persistence_conversion_with_encryption():
    task_id = 'task-123'
    v10_config = pb2_v10.TaskPushNotificationConfig(
        id='pnc-1',
        url='https://example.com/push',
        token='secret-token',
    )
    owner = 'owner-789'
    key = Fernet.generate_key()
    fernet = Fernet(key)

    # Test Core -> Model (With encryption)
    model = core_to_compat_push_notification_config_model(
        task_id, v10_config, owner, fernet=fernet
    )
    assert (
        model.config_data != v10_config.SerializeToString()
    )  # Should be encrypted

    # Decrypt and verify
    decrypted_data = fernet.decrypt(model.config_data)

    data = json.loads(decrypted_data.decode('utf-8'))
    assert data['url'] == 'https://example.com/push'
    assert data['token'] == 'secret-token'

    # Test Model -> Core
    v10_restored = compat_push_notification_config_model_to_core(
        decrypted_data.decode('utf-8'), task_id
    )
    assert v10_restored.id == v10_config.id
    assert v10_restored.url == v10_config.url
    assert v10_restored.token == v10_config.token


def test_to_compat_agent_card_unsupported_version():
    card = pb2_v10.AgentCard(
        name='Modern Agent',
        description='Only supports 1.0',
        version='1.0.0',
        supported_interfaces=[
            pb2_v10.AgentInterface(
                url='http://grpc.v10.com',
                protocol_binding='GRPC',
                protocol_version='1.0.0',
            ),
        ],
        capabilities=pb2_v10.AgentCapabilities(),
    )
    with pytest.raises(
        VersionNotSupportedError,
        match='AgentCard must have at least one interface with compatible protocol version.',
    ):
        to_compat_agent_card(card)
