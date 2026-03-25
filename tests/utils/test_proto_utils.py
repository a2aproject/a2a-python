"""Tests for a2a.utils.proto_utils module.

This module tests the proto utilities including to_stream_response and dictionary normalization.
"""

import httpx
import pytest

from google.protobuf.json_format import MessageToDict, Parse
from google.protobuf.message import Message as ProtobufMessage
from google.protobuf.timestamp_pb2 import Timestamp
from starlette.datastructures import QueryParams

from a2a.types.a2a_pb2 import (
    AgentSkill,
    ListTasksRequest,
    Message,
    Part,
    Role,
    StreamResponse,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from a2a.utils import proto_utils
from a2a.utils.errors import InvalidParamsError


class TestToStreamResponse:
    """Tests for to_stream_response function."""

    def test_stream_response_with_task(self):
        """Test to_stream_response with a Task event."""
        task = Task(
            id='task-1',
            context_id='ctx-1',
            status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
        )
        result = proto_utils.to_stream_response(task)

        assert isinstance(result, StreamResponse)
        assert result.HasField('task')
        assert result.task.id == 'task-1'

    def test_stream_response_with_message(self):
        """Test to_stream_response with a Message event."""
        message = Message(
            message_id='msg-1',
            role=Role.ROLE_AGENT,
            parts=[Part(text='Hello')],
        )
        result = proto_utils.to_stream_response(message)

        assert isinstance(result, StreamResponse)
        assert result.HasField('message')
        assert result.message.message_id == 'msg-1'

    def test_stream_response_with_status_update(self):
        """Test to_stream_response with a TaskStatusUpdateEvent."""
        status_update = TaskStatusUpdateEvent(
            task_id='task-1',
            context_id='ctx-1',
            status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
        )
        result = proto_utils.to_stream_response(status_update)

        assert isinstance(result, StreamResponse)
        assert result.HasField('status_update')
        assert result.status_update.task_id == 'task-1'

    def test_stream_response_with_artifact_update(self):
        """Test to_stream_response with a TaskArtifactUpdateEvent."""
        artifact_update = TaskArtifactUpdateEvent(
            task_id='task-1',
            context_id='ctx-1',
        )
        result = proto_utils.to_stream_response(artifact_update)

        assert isinstance(result, StreamResponse)
        assert result.HasField('artifact_update')
        assert result.artifact_update.task_id == 'task-1'


class TestDictSerialization:
    """Tests for serialization utility functions."""

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

        assert result['string'] == 'hello'
        assert result['int'] == 42
        assert result['float'] == 3.14
        assert result['bool'] is True
        assert result['none'] is None

        assert result['custom'] == 'custom_str'
        assert result['list'] == [1, 'two', 'custom_str']
        assert result['tuple'] == [1, 2, 'custom_str']
        assert result['nested']['inner_custom'] == 'custom_str'
        assert result['nested']['inner_normal'] == 'value'

    def test_normalize_large_integers_to_strings(self):
        """Test the normalize_large_integers_to_strings utility function."""

        test_data = {
            'small_int': 42,
            'large_int': 9999999999999999999,
            'negative_large': -9999999999999999999,
            'float': 3.14,
            'string': 'hello',
            'list': [123, 9999999999999999999, 'text'],
            'nested': {'inner_large': 9999999999999999999, 'inner_small': 100},
        }

        result = proto_utils.normalize_large_integers_to_strings(test_data)

        assert result['small_int'] == 42
        assert isinstance(result['small_int'], int)

        assert result['large_int'] == '9999999999999999999'
        assert isinstance(result['large_int'], str)
        assert result['negative_large'] == '-9999999999999999999'
        assert isinstance(result['negative_large'], str)

        assert result['float'] == 3.14
        assert result['string'] == 'hello'
        assert result['list'] == [123, '9999999999999999999', 'text']
        assert result['nested']['inner_large'] == '9999999999999999999'
        assert result['nested']['inner_small'] == 100

    def test_parse_string_integers_in_dict(self):
        """Test the parse_string_integers_in_dict utility function."""

        test_data = {
            'regular_string': 'hello',
            'numeric_string_small': '123',
            'numeric_string_large': '9999999999999999999',
            'negative_large_string': '-9999999999999999999',
            'float_string': '3.14',
            'mixed_string': '123abc',
            'int': 42,
            'list': ['hello', '9999999999999999999', '123'],
            'nested': {
                'inner_large_string': '9999999999999999999',
                'inner_regular': 'value',
            },
        }

        result = proto_utils.parse_string_integers_in_dict(test_data)

        assert result['regular_string'] == 'hello'
        assert result['numeric_string_small'] == '123'
        assert result['float_string'] == '3.14'
        assert result['mixed_string'] == '123abc'

        assert result['numeric_string_large'] == 9999999999999999999
        assert isinstance(result['numeric_string_large'], int)
        assert result['negative_large_string'] == -9999999999999999999
        assert isinstance(result['negative_large_string'], int)

        assert result['int'] == 42
        assert result['list'] == ['hello', 9999999999999999999, '123']
        assert result['nested']['inner_large_string'] == 9999999999999999999


class TestRestParams:
    """Unit tests for REST parameter conversion."""

    def test_rest_params_roundtrip(self):
        """Test the comprehensive roundtrip conversion for REST parameters."""

        original = ListTasksRequest(
            tenant='tenant-1',
            context_id='ctx-1',
            status=TaskState.TASK_STATE_WORKING,
            page_size=10,
            include_artifacts=True,
            status_timestamp_after=Parse('"2024-03-09T16:00:00Z"', Timestamp()),
            history_length=5,
        )

        query_params = self._message_to_rest_params(original)

        assert dict(query_params) == {
            'tenant': 'tenant-1',
            'contextId': 'ctx-1',
            'status': 'TASK_STATE_WORKING',
            'pageSize': '10',
            'includeArtifacts': 'true',
            'statusTimestampAfter': '2024-03-09T16:00:00Z',
            'historyLength': '5',
        }

        converted = ListTasksRequest()
        proto_utils.parse_params(QueryParams(query_params), converted)

        assert converted == original

    @pytest.mark.parametrize(
        'query_string',
        [
            'id=skill-1&tags=tag1&tags=tag2&tags=tag3',
            'id=skill-1&tags=tag1,tag2,tag3',
        ],
    )
    def test_repeated_fields_parsing(self, query_string: str):
        """Test parsing of repeated fields using different query string formats."""
        query_params = QueryParams(query_string)

        converted = AgentSkill()
        proto_utils.parse_params(query_params, converted)

        assert converted == AgentSkill(
            id='skill-1', tags=['tag1', 'tag2', 'tag3']
        )

    def _message_to_rest_params(self, message: ProtobufMessage) -> QueryParams:
        """Converts a message to REST query parameters."""
        rest_dict = MessageToDict(message)
        return httpx.Request(
            'GET', 'http://api.example.com', params=rest_dict
        ).url.params


class TestValidateProtoRequiredFields:
    """Tests for validate_proto_required_fields function."""

    def test_valid_required_fields(self):
        """Test with all required fields present."""
        msg = Message(
            message_id='msg-1',
            role=Role.ROLE_USER,
            parts=[Part(text='hello')],
        )
        proto_utils.validate_proto_required_fields(msg)

    def test_missing_required_fields(self):
        """Test with empty message raising InvalidParamsError containing all errors."""
        msg = Message()
        with pytest.raises(InvalidParamsError) as exc_info:
            proto_utils.validate_proto_required_fields(msg)

        err = exc_info.value
        errors = err.data.get('errors', []) if err.data else []

        assert {e['field'] for e in errors} == {'message_id', 'role', 'parts'}

    def test_nested_required_fields(self):
        """Test nested required fields inside TaskStatus."""
        # Task Status requires 'state'
        task = Task(id='task-1', status=TaskStatus())
        with pytest.raises(InvalidParamsError) as exc_info:
            proto_utils.validate_proto_required_fields(task)

        err = exc_info.value
        errors = err.data.get('errors', []) if err.data else []

        fields = [e['field'] for e in errors]
        assert 'status.state' in fields
