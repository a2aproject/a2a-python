"""Tests for Kafka request handler."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from a2a.server.context import ServerCallContext
from a2a.server.request_handlers.kafka_handler import KafkaHandler, KafkaMessage, ResponseSender
from a2a.server.request_handlers.request_handler import RequestHandler
from a2a.types import (
    AgentCard,
    AgentCapabilities,
    Artifact,
    DeleteTaskPushNotificationConfigParams,
    GetTaskPushNotificationConfigParams,
    ListTaskPushNotificationConfigParams,
    Message,
    MessageSendParams,
    Part,
    PushNotificationConfig,
    Role,
    Task,
    TaskArtifactUpdateEvent,
    TaskIdParams,
    TaskPushNotificationConfig,
    TaskQueryParams,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
    TextPart,
    TransportProtocol,
)
from a2a.utils.errors import ServerError


class MockResponseSender:
    """Mock implementation of ResponseSender for testing."""
    
    def __init__(self):
        self.sent_responses = []
        self.sent_errors = []
        self.stream_completions = []
    
    async def send_response(
        self,
        reply_topic: str,
        correlation_id: str,
        result: any,
        response_type: str,
    ) -> None:
        self.sent_responses.append({
            'reply_topic': reply_topic,
            'correlation_id': correlation_id,
            'result': result,
            'response_type': response_type
        })
    
    async def send_error_response(
        self,
        reply_topic: str,
        correlation_id: str,
        error_message: str,
    ) -> None:
        self.sent_errors.append({
            'reply_topic': reply_topic,
            'correlation_id': correlation_id,
            'error_message': error_message
        })
    
    async def send_stream_complete(
        self,
        reply_topic: str,
        correlation_id: str,
    ) -> None:
        self.stream_completions.append({
            'reply_topic': reply_topic,
            'correlation_id': correlation_id
        })


@pytest.fixture
def mock_request_handler():
    """Create a mock request handler."""
    return AsyncMock(spec=RequestHandler)


@pytest.fixture
def mock_response_sender():
    """Create a mock response sender."""
    return MockResponseSender()


@pytest.fixture
def kafka_handler(mock_request_handler, mock_response_sender):
    """Create a KafkaHandler instance for testing."""
    return KafkaHandler(mock_request_handler, mock_response_sender)


@pytest.fixture
def sample_agent_card():
    """Create a sample agent card for testing."""
    return AgentCard(
        name="Test Agent",
        description="Test agent for Kafka handler",
        url="kafka://localhost:9092/test-requests",
        version="1.0.0",
        capabilities=AgentCapabilities(streaming=True),
        skills=[],
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        preferred_transport=TransportProtocol.kafka,
    )


@pytest.fixture
def sample_message():
    """Create a sample message for testing."""
    return Message(
        message_id="msg-1",
        role=Role.user,
        parts=[Part(root=TextPart(text="Hello, world!"))],
    )


@pytest.fixture
def sample_task():
    """Create a sample task for testing."""
    return Task(
        id="task-123",
        context_id="ctx-456",
        status=TaskStatus(state=TaskState.completed),
    )


class TestKafkaMessage:
    """Test KafkaMessage class."""

    def test_init(self):
        """Test KafkaMessage initialization."""
        headers = [("correlation_id", b"test-id"), ("reply_topic", b"test-topic")]
        value = {"method": "test_method", "params": {}}
        
        message = KafkaMessage(headers, value)
        
        assert message.headers == headers
        assert message.value == value

    def test_get_header_existing(self):
        """Test getting an existing header."""
        headers = [("correlation_id", b"test-id"), ("reply_topic", b"test-topic")]
        value = {}
        
        message = KafkaMessage(headers, value)
        
        assert message.get_header("correlation_id") == "test-id"
        assert message.get_header("reply_topic") == "test-topic"

    def test_get_header_nonexistent(self):
        """Test getting a non-existent header."""
        headers = [("correlation_id", b"test-id")]
        value = {}
        
        message = KafkaMessage(headers, value)
        
        assert message.get_header("nonexistent") is None


class TestKafkaHandler:
    """Test KafkaHandler class."""

    def test_init(self, mock_request_handler, mock_response_sender):
        """Test KafkaHandler initialization."""
        handler = KafkaHandler(mock_request_handler, mock_response_sender)
        
        assert handler.request_handler == mock_request_handler
        assert handler.response_sender == mock_response_sender

    def test_handle_request_missing_headers(self, kafka_handler, mock_response_sender):
        """Test handling request with missing required headers."""
        # Missing correlation_id
        headers = [("reply_topic", b"test-topic")]
        value = {"method": "message_send", "params": {}}
        message = KafkaMessage(headers, value)
        
        asyncio.run(kafka_handler.handle_request(message))
        
        # Should not send any response due to missing correlation_id
        assert len(mock_response_sender.sent_responses) == 0
        assert len(mock_response_sender.sent_errors) == 0

    def test_handle_request_missing_method(self, kafka_handler, mock_response_sender):
        """Test handling request with missing method."""
        headers = [
            ("correlation_id", b"test-id"),
            ("reply_topic", b"test-topic")
        ]
        value = {"params": {}}  # Missing method
        message = KafkaMessage(headers, value)
        
        asyncio.run(kafka_handler.handle_request(message))
        
        # Should send error response
        assert len(mock_response_sender.sent_errors) == 1
        assert mock_response_sender.sent_errors[0]["error_message"] == "Missing method in request"

    def test_handle_message_send_single(self, kafka_handler, mock_request_handler, mock_response_sender, sample_task, sample_message):
        """Test handling single message_send request."""
        # Setup mock
        mock_request_handler.on_message_send.return_value = sample_task
        
        headers = [
            ("correlation_id", b"test-id"),
            ("reply_topic", b"test-topic"),
            ("agent_id", b"test-agent"),
            ("trace_id", b"test-trace")
        ]
        value = {
            "method": "message_send",
            "params": {
                "message": sample_message.model_dump()
            },
            "streaming": False
        }
        message = KafkaMessage(headers, value)
        
        asyncio.run(kafka_handler.handle_request(message))
        
        # Verify request handler was called
        mock_request_handler.on_message_send.assert_called_once()
        call_args = mock_request_handler.on_message_send.call_args
        assert isinstance(call_args[0][0], MessageSendParams)
        assert isinstance(call_args[0][1], ServerCallContext)
        
        # Verify response was sent
        assert len(mock_response_sender.sent_responses) == 1
        response = mock_response_sender.sent_responses[0]
        assert response["reply_topic"] == "test-topic"
        assert response["correlation_id"] == "test-id"
        assert response["result"] == sample_task
        assert response["response_type"] == "task"

    def test_handle_message_send_streaming(self, kafka_handler, mock_request_handler, mock_response_sender, sample_task, sample_message):
        """Test handling streaming message_send request."""
        # Setup mock to return async generator
        async def mock_stream():
            yield sample_task
            yield TaskStatusUpdateEvent(
                task_id="task-123",
                context_id="ctx-456",
                status=TaskStatus(state=TaskState.working),
                final=False
            )
        
        mock_request_handler.on_message_send_stream.return_value = mock_stream()
        
        headers = [
            ("correlation_id", b"test-id"),
            ("reply_topic", b"test-topic")
        ]
        value = {
            "method": "message_send",
            "params": {
                "message": sample_message.model_dump()
            },
            "streaming": True
        }
        message = KafkaMessage(headers, value)
        
        asyncio.run(kafka_handler.handle_request(message))
        
        # Verify request handler was called
        mock_request_handler.on_message_send_stream.assert_called_once()
        
        # Verify responses were sent
        assert len(mock_response_sender.sent_responses) == 2
        assert mock_response_sender.sent_responses[0]["response_type"] == "task"
        assert mock_response_sender.sent_responses[1]["response_type"] == "task_status_update"
        
        # Verify stream completion was sent
        assert len(mock_response_sender.stream_completions) == 1
        assert mock_response_sender.stream_completions[0]["correlation_id"] == "test-id"

    def test_handle_task_get(self, kafka_handler, mock_request_handler, mock_response_sender, sample_task):
        """Test handling task_get request."""
        mock_request_handler.on_get_task.return_value = sample_task
        
        headers = [
            ("correlation_id", b"test-id"),
            ("reply_topic", b"test-topic")
        ]
        value = {
            "method": "task_get",
            "params": {
                "id": "task-123"
            },
            "streaming": False
        }
        message = KafkaMessage(headers, value)
        
        asyncio.run(kafka_handler.handle_request(message))
        
        # Verify request handler was called
        mock_request_handler.on_get_task.assert_called_once()
        call_args = mock_request_handler.on_get_task.call_args
        assert isinstance(call_args[0][0], TaskQueryParams)
        assert call_args[0][0].id == "task-123"
        
        # Verify response was sent
        assert len(mock_response_sender.sent_responses) == 1
        response = mock_response_sender.sent_responses[0]
        assert response["result"] == sample_task
        assert response["response_type"] == "task"

    def test_handle_task_cancel(self, kafka_handler, mock_request_handler, mock_response_sender, sample_task):
        """Test handling task_cancel request."""
        mock_request_handler.on_cancel_task.return_value = sample_task
        
        headers = [
            ("correlation_id", b"test-id"),
            ("reply_topic", b"test-topic")
        ]
        value = {
            "method": "task_cancel",
            "params": {
                "id": "task-123"
            },
            "streaming": False
        }
        message = KafkaMessage(headers, value)
        
        asyncio.run(kafka_handler.handle_request(message))
        
        # Verify request handler was called
        mock_request_handler.on_cancel_task.assert_called_once()
        call_args = mock_request_handler.on_cancel_task.call_args
        assert isinstance(call_args[0][0], TaskIdParams)
        assert call_args[0][0].id == "task-123"
        
        # Verify response was sent
        assert len(mock_response_sender.sent_responses) == 1
        response = mock_response_sender.sent_responses[0]
        assert response["result"] == sample_task
        assert response["response_type"] == "task"

    def test_handle_push_notification_config_get(self, kafka_handler, mock_request_handler, mock_response_sender):
        """Test handling task_push_notification_config_get request."""
        config = TaskPushNotificationConfig(
            task_id="task-123",
            push_notification_config=PushNotificationConfig(url="http://example.com/webhook")
        )
        mock_request_handler.on_get_task_push_notification_config.return_value = config
        
        headers = [
            ("correlation_id", b"test-id"),
            ("reply_topic", b"test-topic")
        ]
        value = {
            "method": "task_push_notification_config_get",
            "params": {
                "id": "task-123"
            },
            "streaming": False
        }
        message = KafkaMessage(headers, value)
        
        asyncio.run(kafka_handler.handle_request(message))
        
        # Verify request handler was called
        mock_request_handler.on_get_task_push_notification_config.assert_called_once()
        
        # Verify response was sent
        assert len(mock_response_sender.sent_responses) == 1
        response = mock_response_sender.sent_responses[0]
        assert response["result"] == config
        assert response["response_type"] == "task_push_notification_config"

    def test_handle_push_notification_config_list(self, kafka_handler, mock_request_handler, mock_response_sender):
        """Test handling task_push_notification_config_list request."""
        configs = [
            TaskPushNotificationConfig(
                task_id="task-123",
                push_notification_config=PushNotificationConfig(url="http://example.com/webhook1")
            ),
            TaskPushNotificationConfig(
                task_id="task-456",
                push_notification_config=PushNotificationConfig(url="http://example.com/webhook2")
            )
        ]
        mock_request_handler.on_list_task_push_notification_config.return_value = configs
        
        headers = [
            ("correlation_id", b"test-id"),
            ("reply_topic", b"test-topic")
        ]
        value = {
            "method": "task_push_notification_config_list",
            "params": {"id": "task-123"},
            "streaming": False
        }
        message = KafkaMessage(headers, value)
        
        asyncio.run(kafka_handler.handle_request(message))
        
        # Verify request handler was called
        mock_request_handler.on_list_task_push_notification_config.assert_called_once()
        
        # Verify response was sent
        assert len(mock_response_sender.sent_responses) == 1
        response = mock_response_sender.sent_responses[0]
        assert response["result"] == configs
        assert response["response_type"] == "task_push_notification_config_list"

    def test_handle_push_notification_config_set(self, kafka_handler, mock_request_handler, mock_response_sender):
        """Test handling task_push_notification_config_set request."""
        config = TaskPushNotificationConfig(
            task_id="task-123",
            push_notification_config=PushNotificationConfig(url="http://example.com/webhook")
        )
        mock_request_handler.on_set_task_push_notification_config.return_value = config

        headers = [
            ("correlation_id", b"test-id"),
            ("reply_topic", b"test-topic")
        ]
        value = {
            "method": "task_push_notification_config_set",
            "params": config.model_dump(),
            "streaming": False
        }
        message = KafkaMessage(headers, value)

        asyncio.run(kafka_handler.handle_request(message))

        # Verify request handler was called with proper model instance
        mock_request_handler.on_set_task_push_notification_config.assert_called_once()
        call_args = mock_request_handler.on_set_task_push_notification_config.call_args
        assert isinstance(call_args[0][0], TaskPushNotificationConfig)
        assert call_args[0][0].task_id == "task-123"

        # Verify response was sent
        assert len(mock_response_sender.sent_responses) == 1
        response = mock_response_sender.sent_responses[0]
        assert response["result"] == config
        assert response["response_type"] == "task_push_notification_config"

    def test_handle_push_notification_config_delete(self, kafka_handler, mock_request_handler, mock_response_sender):
        """Test handling task_push_notification_config_delete request."""
        headers = [
            ("correlation_id", b"test-id"),
            ("reply_topic", b"test-topic")
        ]
        value = {
            "method": "task_push_notification_config_delete",
            "params": {
                "id": "task-123",
                "push_notification_config_id": "cfg-1"
            },
            "streaming": False
        }
        message = KafkaMessage(headers, value)
        
        asyncio.run(kafka_handler.handle_request(message))
        
        # Verify request handler was called
        mock_request_handler.on_delete_task_push_notification_config.assert_called_once()
        call_args = mock_request_handler.on_delete_task_push_notification_config.call_args
        assert isinstance(call_args[0][0], DeleteTaskPushNotificationConfigParams)
        assert call_args[0][0].id == "task-123"
        
        # Verify response was sent
        assert len(mock_response_sender.sent_responses) == 1
        response = mock_response_sender.sent_responses[0]
        assert response["result"] == {"success": True}
        assert response["response_type"] == "success"

    def test_handle_unknown_method(self, kafka_handler, mock_response_sender):
        """Test handling request with unknown method."""
        headers = [
            ("correlation_id", b"test-id"),
            ("reply_topic", b"test-topic")
        ]
        value = {
            "method": "unknown_method",
            "params": {},
            "streaming": False
        }
        message = KafkaMessage(headers, value)
        
        asyncio.run(kafka_handler.handle_request(message))
        
        # Should send error response
        assert len(mock_response_sender.sent_errors) == 1
        assert "Unknown method: unknown_method" in mock_response_sender.sent_errors[0]["error_message"]

    def test_handle_request_with_agent_card(self, kafka_handler, mock_request_handler, mock_response_sender, sample_agent_card, sample_task, sample_message):
        """Test handling request with agent card in payload."""
        mock_request_handler.on_message_send.return_value = sample_task
        
        headers = [
            ("correlation_id", b"test-id"),
            ("reply_topic", b"test-topic")
        ]
        value = {
            "method": "message_send",
            "params": {
                "message": sample_message.model_dump()
            },
            "streaming": False,
            "agent_card": sample_agent_card.model_dump()
        }
        message = KafkaMessage(headers, value)
        
        asyncio.run(kafka_handler.handle_request(message))
        
        # Verify request was processed successfully
        assert len(mock_response_sender.sent_responses) == 1
        assert len(mock_response_sender.sent_errors) == 0

    def test_handle_request_with_invalid_agent_card(self, kafka_handler, mock_request_handler, mock_response_sender, sample_task, sample_message):
        """Test handling request with invalid agent card."""
        mock_request_handler.on_message_send.return_value = sample_task
        
        headers = [
            ("correlation_id", b"test-id"),
            ("reply_topic", b"test-topic")
        ]
        value = {
            "method": "message_send",
            "params": {
                "message": sample_message.model_dump()
            },
            "streaming": False,
            "agent_card": {"invalid": "data"}  # Invalid agent card
        }
        message = KafkaMessage(headers, value)
        
        asyncio.run(kafka_handler.handle_request(message))
        
        # Should still process the request (agent card is optional)
        assert len(mock_response_sender.sent_responses) == 1

    def test_handle_request_handler_exception(self, kafka_handler, mock_request_handler, mock_response_sender, sample_message):
        """Test handling when request handler raises an exception."""
        mock_request_handler.on_message_send.side_effect = Exception("Handler error")
        
        headers = [
            ("correlation_id", b"test-id"),
            ("reply_topic", b"test-topic")
        ]
        value = {
            "method": "message_send",
            "params": {
                "message": sample_message.model_dump()
            },
            "streaming": False
        }
        message = KafkaMessage(headers, value)
        
        asyncio.run(kafka_handler.handle_request(message))
        
        # Should send error response
        assert len(mock_response_sender.sent_errors) == 1
        assert "Handler error" in mock_response_sender.sent_errors[0]["error_message"]

    def test_handle_streaming_unknown_method(self, kafka_handler, mock_response_sender):
        """Test handling streaming request with unknown method."""
        headers = [
            ("correlation_id", b"test-id"),
            ("reply_topic", b"test-topic")
        ]
        value = {
            "method": "unknown_streaming_method",
            "params": {},
            "streaming": True
        }
        message = KafkaMessage(headers, value)
        
        asyncio.run(kafka_handler.handle_request(message))
        
        # Should send error response
        assert len(mock_response_sender.sent_errors) == 1
        assert "Streaming not supported for method: unknown_streaming_method" in mock_response_sender.sent_errors[0]["error_message"]

    def test_handle_streaming_exception(self, kafka_handler, mock_request_handler, mock_response_sender, sample_message):
        """Test handling streaming request when handler raises exception."""
        mock_request_handler.on_message_send_stream.side_effect = Exception("Streaming error")
        
        headers = [
            ("correlation_id", b"test-id"),
            ("reply_topic", b"test-topic")
        ]
        value = {
            "method": "message_send",
            "params": {
                "message": sample_message.model_dump()
            },
            "streaming": True
        }
        message = KafkaMessage(headers, value)
        
        asyncio.run(kafka_handler.handle_request(message))
        
        # Should send error response
        assert len(mock_response_sender.sent_errors) == 1
        assert "Streaming error" in mock_response_sender.sent_errors[0]["error_message"]

    def test_handle_streaming_with_different_event_types(self, kafka_handler, mock_request_handler, mock_response_sender, sample_message, sample_task):
        """Test handling streaming request with different event types."""
        # Setup mock to return different event types
        async def mock_stream():
            yield sample_task
            yield TaskStatusUpdateEvent(
                task_id="task-123",
                context_id="ctx-456",
                status=TaskStatus(state=TaskState.working),
                final=False
            )
            yield TaskArtifactUpdateEvent(
                task_id="task-123",
                context_id="ctx-456",
                artifact=Artifact(
                    artifact_id="artifact-1",
                    parts=[Part(root=TextPart(text="artifact content"))]
                )
            )
            yield Message(
                message_id="msg-2",
                role=Role.agent,
                parts=[Part(root=TextPart(text="Assistant response"))]
            )
        
        mock_request_handler.on_message_send_stream.return_value = mock_stream()
        
        headers = [
            ("correlation_id", b"test-id"),
            ("reply_topic", b"test-topic")
        ]
        value = {
            "method": "message_send",
            "params": {
                "message": sample_message.model_dump()
            },
            "streaming": True
        }
        message = KafkaMessage(headers, value)
        
        asyncio.run(kafka_handler.handle_request(message))
        
        # Verify all event types were handled correctly
        assert len(mock_response_sender.sent_responses) == 4
        assert mock_response_sender.sent_responses[0]["response_type"] == "task"
        assert mock_response_sender.sent_responses[1]["response_type"] == "task_status_update"
        assert mock_response_sender.sent_responses[2]["response_type"] == "task_artifact_update"
        assert mock_response_sender.sent_responses[3]["response_type"] == "message"
        
        # Verify stream completion was sent
        assert len(mock_response_sender.stream_completions) == 1

    def test_handle_task_resubscribe_streaming(self, kafka_handler, mock_request_handler, mock_response_sender):
        """Test handling streaming task_resubscribe request with multiple event types and stream completion."""
        # Setup mock to return async generator with multiple event types
        async def mock_stream():
            yield Task(
                id="task-123",
                context_id="ctx-456",
                status=TaskStatus(state=TaskState.working),
            )
            yield TaskStatusUpdateEvent(
                task_id="task-123",
                context_id="ctx-456",
                status=TaskStatus(state=TaskState.working),
                final=False,
            )
            yield TaskArtifactUpdateEvent(
                task_id="task-123",
                context_id="ctx-456",
                artifact=Artifact(
                    artifact_id="artifact-1",
                    parts=[Part(root=TextPart(text="chunk"))],
                ),
            )

        mock_request_handler.on_resubscribe_to_task.return_value = mock_stream()

        headers = [
            ("correlation_id", b"test-id"),
            ("reply_topic", b"test-topic"),
        ]
        value = {
            "method": "task_resubscribe",
            "params": {"id": "task-123"},
            "streaming": True,
        }
        message = KafkaMessage(headers, value)

        asyncio.run(kafka_handler.handle_request(message))

        # Verify request handler was called
        mock_request_handler.on_resubscribe_to_task.assert_called_once()

        # Verify responses were sent for each yielded event
        assert len(mock_response_sender.sent_responses) == 3
        assert mock_response_sender.sent_responses[0]["response_type"] == "task"
        assert mock_response_sender.sent_responses[1]["response_type"] == "task_status_update"
        assert mock_response_sender.sent_responses[2]["response_type"] == "task_artifact_update"

        # Verify stream completion was sent
        assert len(mock_response_sender.stream_completions) == 1

    def test_server_call_context_creation(self, kafka_handler, mock_request_handler, mock_response_sender, sample_task, sample_message):
        """Test that ServerCallContext is created with correct parameters."""
        mock_request_handler.on_message_send.return_value = sample_task
        
        headers = [
            ("correlation_id", b"test-id"),
            ("reply_topic", b"test-topic"),
            ("agent_id", b"test-agent-123"),
            ("trace_id", b"trace-456")
        ]
        value = {
            "method": "message_send",
            "params": {
                "message": sample_message.model_dump()
            },
            "streaming": False
        }
        message = KafkaMessage(headers, value)
        
        asyncio.run(kafka_handler.handle_request(message))
        
        # Verify ServerCallContext was created and passed to handler
        mock_request_handler.on_message_send.assert_called_once()
        call_args = mock_request_handler.on_message_send.call_args
        context = call_args[0][1]
        assert isinstance(context, ServerCallContext)
