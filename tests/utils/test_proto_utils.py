"""Tests for a2a.utils.proto_utils module.

Since we now use proto types directly as our internal types, most of these
conversions are identity operations. This test module verifies the utilities
that still perform meaningful transformations.
"""

import pytest

from a2a.types import a2a_pb2
from a2a.types.a2a_pb2 import (
    CancelTaskRequest,
    GetTaskRequest,
    Message,
    Part,
    PushNotificationConfig,
    Role,
    SendMessageRequest,
    SetTaskPushNotificationConfigRequest,
    StreamResponse,
    SubscribeToTaskRequest,
    Task,
    TaskArtifactUpdateEvent,
    TaskPushNotificationConfig,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from a2a.utils import proto_utils


class TestFromProto:
    """Tests for FromProto conversion utilities."""

    def test_message_send_params_identity(self):
        """Test that message_send_params is an identity operation."""
        request = SendMessageRequest(
            request=Message(
                message_id='msg-1',
                role=Role.ROLE_USER,
                parts=[Part(text='Hello')],
            )
        )
        result = proto_utils.FromProto.message_send_params(request)
        assert result == request
        assert result is request

    def test_task_id_params_identity(self):
        """Test that task_id_params is an identity operation."""
        cancel_request = CancelTaskRequest(name='tasks/task-1')
        result = proto_utils.FromProto.task_id_params(cancel_request)
        assert result == cancel_request
        assert result is cancel_request

        subscribe_request = SubscribeToTaskRequest(name='tasks/task-2')
        result = proto_utils.FromProto.task_id_params(subscribe_request)
        assert result == subscribe_request

        get_request = GetTaskRequest(name='tasks/task-3')
        result = proto_utils.FromProto.task_id_params(get_request)
        assert result == get_request

    def test_task_push_notification_config_request(self):
        """Test extraction of config from SetTaskPushNotificationConfigRequest."""
        config = TaskPushNotificationConfig(
            name='tasks/task-1/push_notification_config',
            push_notification_config=PushNotificationConfig(
                url='https://example.com/webhook'
            ),
        )
        request = SetTaskPushNotificationConfigRequest(config=config)

        result = proto_utils.FromProto.task_push_notification_config_request(
            request
        )
        assert result == config

    def test_task_push_notification_config_request_empty(self):
        """Test extraction when config is empty."""
        request = SetTaskPushNotificationConfigRequest()

        result = proto_utils.FromProto.task_push_notification_config_request(
            request
        )
        # Should return an empty TaskPushNotificationConfig
        assert isinstance(result, TaskPushNotificationConfig)


class TestToProto:
    """Tests for ToProto conversion utilities."""

    def test_task_identity(self):
        """Test that task is an identity operation."""
        task = Task(
            id='task-1',
            context_id='ctx-1',
            status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
        )
        result = proto_utils.ToProto.task(task)
        assert result == task
        assert result is task

    def test_message_identity(self):
        """Test that message is an identity operation."""
        message = Message(
            message_id='msg-1',
            role=Role.ROLE_USER,
            parts=[Part(text='Hello')],
        )
        result = proto_utils.ToProto.message(message)
        assert result == message
        assert result is message

    def test_task_or_message_with_task(self):
        """Test task_or_message with a Task."""
        task = Task(
            id='task-1',
            context_id='ctx-1',
            status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
        )
        result = proto_utils.ToProto.task_or_message(task)
        assert result == task
        assert result is task

    def test_task_or_message_with_message(self):
        """Test task_or_message with a Message."""
        message = Message(
            message_id='msg-1',
            role=Role.ROLE_AGENT,
            parts=[Part(text='Response')],
        )
        result = proto_utils.ToProto.task_or_message(message)
        assert result == message
        assert result is message

    def test_task_push_notification_config_identity(self):
        """Test that task_push_notification_config is an identity operation."""
        config = TaskPushNotificationConfig(
            name='tasks/task-1/push_notification_config',
            push_notification_config=PushNotificationConfig(
                url='https://example.com/webhook'
            ),
        )
        result = proto_utils.ToProto.task_push_notification_config(config)
        assert result == config
        assert result is config

    def test_stream_response_with_task(self):
        """Test stream_response with a Task event."""
        task = Task(
            id='task-1',
            context_id='ctx-1',
            status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
        )
        result = proto_utils.ToProto.stream_response(task)

        assert isinstance(result, StreamResponse)
        assert result.HasField('task')
        assert result.task.id == 'task-1'

    def test_stream_response_with_message(self):
        """Test stream_response with a Message event."""
        message = Message(
            message_id='msg-1',
            role=Role.ROLE_AGENT,
            parts=[Part(text='Hello')],
        )
        result = proto_utils.ToProto.stream_response(message)

        assert isinstance(result, StreamResponse)
        assert result.HasField('msg')
        assert result.msg.message_id == 'msg-1'

    def test_stream_response_with_status_update(self):
        """Test stream_response with a TaskStatusUpdateEvent."""
        status_update = TaskStatusUpdateEvent(
            task_id='task-1',
            context_id='ctx-1',
            status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
        )
        result = proto_utils.ToProto.stream_response(status_update)

        assert isinstance(result, StreamResponse)
        assert result.HasField('status_update')
        assert result.status_update.task_id == 'task-1'

    def test_stream_response_with_artifact_update(self):
        """Test stream_response with a TaskArtifactUpdateEvent."""
        artifact_update = TaskArtifactUpdateEvent(
            task_id='task-1',
            context_id='ctx-1',
        )
        result = proto_utils.ToProto.stream_response(artifact_update)

        assert isinstance(result, StreamResponse)
        assert result.HasField('artifact_update')
        assert result.artifact_update.task_id == 'task-1'
