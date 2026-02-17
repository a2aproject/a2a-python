"""Tests for a2a.utils.proto_utils module.

This module tests the proto utilities including to_stream_response and dictionary normalization.
"""

import pytest

from a2a.types.a2a_pb2 import (
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
        assert result['nested']['inner_regular'] == 'value'
