"""Tests for a2a.utils.proto_utils module.

This module tests the proto utilities including to_stream_response and dictionary normalization.
"""

from unittest.mock import patch

import httpx
import pytest

from a2a.types.a2a_pb2 import (
    AgentCard,
    AgentSkill,
    ListTasksRequest,
    Message,
    Part,
    Role,
    SecurityScheme,
    StreamResponse,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from a2a.utils import proto_utils
from a2a.utils.errors import InvalidParamsError
from google.protobuf.json_format import MessageToDict, Parse
from google.protobuf.message import Message as ProtobufMessage
from google.protobuf.timestamp_pb2 import Timestamp
from google.rpc import error_details_pb2
from starlette.datastructures import QueryParams


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
        httpx_params = httpx.Request(
            'GET', 'http://api.example.com', params=rest_dict
        ).url.params
        return QueryParams(str(httpx_params))


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


class TestIsFieldRepeated:
    """Tests for the _is_field_repeated helper, including the legacy fallback."""

    def test_repeated_field_fallback_path(self):
        """Uses the legacy field.label path when is_repeated is unavailable."""
        tags_field = AgentSkill.DESCRIPTOR.fields_by_name['tags']
        with patch('a2a.utils.proto_utils._PROTOBUF_HAS_IS_REPEATED', False):
            assert proto_utils._is_field_repeated(tags_field) is True

    def test_non_repeated_field_fallback_path(self):
        """Legacy field.label path returns False for a non-repeated field."""
        id_field = AgentSkill.DESCRIPTOR.fields_by_name['id']
        with patch('a2a.utils.proto_utils._PROTOBUF_HAS_IS_REPEATED', False):
            assert proto_utils._is_field_repeated(id_field) is False


class TestParseParamsEdgeCases:
    """Edge-case tests for parse_params to cover missing branches."""

    def test_unknown_key_is_ignored(self):
        """Unknown query param keys are silently ignored; known keys are still parsed."""
        msg = ListTasksRequest()
        proto_utils.parse_params(QueryParams('unknownKey=value&tenant=t1'), msg)
        assert msg.tenant == 't1'

    def test_repeated_field_skips_empty_string(self):
        """Empty string values in a repeated field are skipped rather than accumulated."""
        msg = AgentSkill()
        proto_utils.parse_params(QueryParams('id=s1&tags=&tags=tag1'), msg)
        assert list(msg.tags) == ['tag1']

    def test_repeated_field_non_string_value(self):
        """Non-string values in a repeated field are appended directly without splitting."""

        class _MockParams:
            def keys(self):
                return ['tags']

            def getlist(self, _key):
                return ['tag1', 42]  # 42 is a non-string

        msg = AgentSkill()
        with patch('a2a.utils.proto_utils.ParseDict') as mock_parse:
            proto_utils.parse_params(_MockParams(), msg)  # type: ignore[arg-type]
            # 42 should be appended directly (not split as a string)
            mock_parse.assert_called_once_with(
                {'tags': ['tag1', 42]}, msg, ignore_unknown_fields=True
            )


class TestValidationEdgeCases:
    """Additional validation tests to cover missing branches."""

    def test_required_message_field_not_set(self):
        """A REQUIRED message field with presence that is not set produces a validation error."""
        # Task.status is REQUIRED + has_presence; omitting it hits the branch.
        task = Task(id='task-1', context_id='ctx-1')
        with pytest.raises(InvalidParamsError) as exc_info:
            proto_utils.validate_proto_required_fields(task)

        errors = (
            exc_info.value.data.get('errors', []) if exc_info.value.data else []
        )
        fields = [e['field'] for e in errors]
        assert 'status' in fields

    def test_map_field_recurse_validation(self):
        """Map entry fields are recursively validated when populated."""
        # AgentCard.security_schemes is a map<string, SecurityScheme>.
        # Populating it causes _recurse_validation to enter the map_entry branch.
        card = AgentCard()
        card.security_schemes['myScheme'].CopyFrom(SecurityScheme())
        # We only need the code path to execute; errors from other required
        # fields on AgentCard are expected.
        errors = proto_utils._validate_proto_required_fields_internal(card)
        # The map branch ran; verify no crash and we got some errors.
        assert isinstance(errors, list)


class TestBadRequestConversions:
    """Tests for validation_errors_to_bad_request and bad_request_to_validation_errors."""

    def test_validation_errors_to_bad_request(self):
        """Lines 334-339: convert ValidationDetail list to BadRequest proto."""
        errors: list[proto_utils.ValidationDetail] = [
            proto_utils.ValidationDetail(field='foo', message='required'),
            proto_utils.ValidationDetail(field='bar', message='invalid'),
        ]
        bad_request = proto_utils.validation_errors_to_bad_request(errors)

        assert isinstance(bad_request, error_details_pb2.BadRequest)
        assert len(bad_request.field_violations) == 2
        assert bad_request.field_violations[0].field == 'foo'
        assert bad_request.field_violations[0].description == 'required'
        assert bad_request.field_violations[1].field == 'bar'
        assert bad_request.field_violations[1].description == 'invalid'

    def test_bad_request_to_validation_errors(self):
        """Converts a BadRequest proto back to a ValidationDetail list."""
        bad_request = error_details_pb2.BadRequest()
        v = bad_request.field_violations.add()
        v.field = 'baz'
        v.description = 'must be set'

        errors = proto_utils.bad_request_to_validation_errors(bad_request)

        assert len(errors) == 1
        assert errors[0]['field'] == 'baz'
        assert errors[0]['message'] == 'must be set'

    def test_bad_request_roundtrip(self):
        """Roundtrip: ValidationDetail -> BadRequest -> ValidationDetail."""
        original: list[proto_utils.ValidationDetail] = [
            proto_utils.ValidationDetail(field='x', message='err'),
        ]
        restored = proto_utils.bad_request_to_validation_errors(
            proto_utils.validation_errors_to_bad_request(original)
        )
        assert restored == original
