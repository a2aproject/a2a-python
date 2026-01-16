"""Tests for a2a.utils.proto_utils module.

This module tests the to_stream_response function which wraps events
in StreamResponse protos.
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
