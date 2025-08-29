"""Tests for Kafka client transport."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from a2a.client.transports.kafka import KafkaClientTransport
from a2a.client.transports.kafka_correlation import CorrelationManager
from a2a.client.errors import A2AClientError
from a2a.types import (
    AgentCard,
    AgentCapabilities,
    Message,
    MessageSendParams,
    Part,
    Role,
    Task,
    TaskState,
    TaskStatus,
    TextPart,
    TransportProtocol,
)


@pytest.fixture
def agent_card():
    """Create test agent card."""
    return AgentCard(
        name="Test Agent",
        description="Test agent for Kafka transport",
        url="kafka://localhost:9092/test-requests",
        version="1.0.0",
        capabilities=AgentCapabilities(streaming=True),
        skills=[],
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        preferred_transport=TransportProtocol.kafka,
    )


@pytest.fixture
def kafka_transport(agent_card):
    """Create Kafka transport instance."""
    return KafkaClientTransport(
        agent_card=agent_card,
        bootstrap_servers="localhost:9092",
        request_topic="test-requests",
        reply_topic_prefix="test-reply"
    )


class TestCorrelationManager:
    """Test correlation manager functionality."""

    @pytest.mark.asyncio
    async def test_generate_correlation_id(self):
        """Test correlation ID generation."""
        manager = CorrelationManager()
        
        # Generate multiple IDs
        id1 = manager.generate_correlation_id()
        id2 = manager.generate_correlation_id()
        
        # Should be different
        assert id1 != id2
        assert len(id1) > 0
        assert len(id2) > 0

    @pytest.mark.asyncio
    async def test_register_and_complete(self):
        """Test request registration and completion."""
        manager = CorrelationManager()
        correlation_id = manager.generate_correlation_id()
        
        # Register request
        future = await manager.register(correlation_id)
        assert not future.done()
        assert manager.get_pending_count() == 1
        
        # Complete request
        result = Message(
            message_id="msg-1",
            role=Role.assistant,
            parts=[Part(root=TextPart(text="test response"))],
        )
        completed = await manager.complete(correlation_id, result)
        
        assert completed is True
        assert future.done()
        assert await future == result
        assert manager.get_pending_count() == 0

    @pytest.mark.asyncio
    async def test_complete_with_exception(self):
        """Test completing request with exception."""
        manager = CorrelationManager()
        correlation_id = manager.generate_correlation_id()
        
        # Register request
        future = await manager.register(correlation_id)
        
        # Complete with exception
        exception = Exception("test error")
        completed = await manager.complete_with_exception(correlation_id, exception)
        
        assert completed is True
        assert future.done()
        
        with pytest.raises(Exception) as exc_info:
            await future
        assert str(exc_info.value) == "test error"

    @pytest.mark.asyncio
    async def test_cancel_all(self):
        """Test cancelling all pending requests."""
        manager = CorrelationManager()
        
        # Register multiple requests
        futures = []
        for i in range(3):
            correlation_id = manager.generate_correlation_id()
            future = await manager.register(correlation_id)
            futures.append(future)
        
        assert manager.get_pending_count() == 3
        
        # Cancel all
        await manager.cancel_all()
        
        assert manager.get_pending_count() == 0
        for future in futures:
            assert future.cancelled()


class TestKafkaClientTransport:
    """Test Kafka client transport functionality."""

    def test_initialization(self, kafka_transport, agent_card):
        """Test transport initialization."""
        assert kafka_transport.agent_card == agent_card
        assert kafka_transport.bootstrap_servers == "localhost:9092"
        assert kafka_transport.request_topic == "test-requests"
        assert kafka_transport.reply_topic is None  # Not set until _start()
        assert not kafka_transport._running
        assert not kafka_transport._auto_start

    @pytest.mark.asyncio
    @patch('a2a.client.transports.kafka.AIOKafkaProducer')
    @patch('a2a.client.transports.kafka.AIOKafkaConsumer')
    async def test_internal_start_stop(self, mock_consumer_class, mock_producer_class, kafka_transport):
        """Test internal starting and stopping of the transport."""
        # Mock producer and consumer
        mock_producer = AsyncMock()
        mock_consumer = AsyncMock()
        mock_producer_class.return_value = mock_producer
        mock_consumer_class.return_value = mock_consumer
        
        # Start transport
        await kafka_transport.start()
        
        assert kafka_transport._running is True
        assert kafka_transport.producer == mock_producer
        assert kafka_transport.consumer == mock_consumer
        # After _start, reply_topic should be generated
        assert kafka_transport.reply_topic is not None
        assert kafka_transport.reply_topic.startswith("test-reply-Test_Agent-")
        mock_producer.start.assert_called_once()
        mock_consumer.start.assert_called_once()
        
        # Stop transport
        await kafka_transport.stop()
        
        assert kafka_transport._running is False
        mock_producer.stop.assert_called_once()
        mock_consumer.stop.assert_called_once()

    @pytest.mark.asyncio
    @patch('a2a.client.transports.kafka.AIOKafkaProducer')
    @patch('a2a.client.transports.kafka.AIOKafkaConsumer')
    async def test_send_message(self, mock_consumer_class, mock_producer_class, kafka_transport):
        """Test sending a message."""
        # Mock producer and consumer
        mock_producer = AsyncMock()
        mock_consumer = AsyncMock()
        mock_producer_class.return_value = mock_producer
        mock_consumer_class.return_value = mock_consumer
        
        # Start transport
        await kafka_transport.start()
        
        # Mock correlation manager
        with patch.object(kafka_transport.correlation_manager, 'generate_correlation_id') as mock_gen_id, \
             patch.object(kafka_transport.correlation_manager, 'register') as mock_register:
            
            mock_gen_id.return_value = "test-correlation-id"
            
            # Create a future that resolves to a response
            response = Message(
                message_id="msg-1",
                role=Role.assistant,
                parts=[Part(root=TextPart(text="test response"))],
            )
            future = asyncio.Future()
            future.set_result(response)
            mock_register.return_value = future
            
            # Send message
            request = MessageSendParams(
                message=Message(
                    message_id="msg-1",
                    role=Role.user,
                    parts=[Part(root=TextPart(text="test message"))],
                )
            )
            result = await kafka_transport.send_message(request)
            
            # Verify result
            assert result == response
            
            # Verify producer was called
            mock_producer.send_and_wait.assert_called_once()
            call_args = mock_producer.send_and_wait.call_args
            
            assert call_args[0][0] == "test-requests"  # topic
            assert call_args[1]['value']['method'] == 'message_send'
            assert 'params' in call_args[1]['value']
            # Verify the message structure is properly serialized
            params = call_args[1]['value']['params']
            assert 'message' in params
            
            # Check headers
            headers = call_args[1]['headers']
            header_dict = {k: v.decode('utf-8') for k, v in headers}
            assert header_dict['correlation_id'] == 'test-correlation-id'
            assert 'reply_topic' in header_dict
            assert header_dict['reply_topic'] is not None

    def test_parse_response(self, kafka_transport):
        """Test response parsing."""
        # Test message response
        message_data = {
            'type': 'message',
            'data': {
                'message_id': 'msg-1',
                'role': 'assistant',
                'parts': [{'root': {'text': 'test response', 'type': 'text'}}]
            }
        }
        result = kafka_transport._parse_response(message_data)
        assert isinstance(result, Message)
        assert result.message_id == 'msg-1'
        assert result.role == Role.assistant
        
        # Test task response
        task_data = {
            'type': 'task',
            'data': {
                'id': 'task-123',
                'context_id': 'ctx-456',
                'status': {'state': 'completed'}
            }
        }
        result = kafka_transport._parse_response(task_data)
        assert isinstance(result, Task)
        assert result.id == 'task-123'
        
        # Test default case (should default to message)
        default_data = {
            'data': {
                'message_id': 'msg-2',
                'role': 'assistant',
                'parts': [{'root': {'text': 'default response', 'type': 'text'}}]
            }
        }
        result = kafka_transport._parse_response(default_data)
        assert isinstance(result, Message)
        assert result.message_id == 'msg-2'

    @pytest.mark.asyncio
    async def test_context_manager(self, kafka_transport):
        """Test async context manager."""
        with patch.object(kafka_transport, 'start') as mock_start, \
             patch.object(kafka_transport, 'stop') as mock_stop:
            
            async with kafka_transport:
                mock_start.assert_called_once()
            
            mock_stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_message_timeout(self, kafka_transport):
        """Test send message with timeout."""
        with patch.object(kafka_transport, '_send_request') as mock_send, \
             patch.object(kafka_transport.correlation_manager, 'register') as mock_register:
            
            # Create a future that never resolves
            future = asyncio.Future()
            mock_register.return_value = future
            mock_send.return_value = "test-correlation-id"
            
            request = MessageSendParams(
                message=Message(
                    message_id="msg-1",
                    role=Role.user,
                    parts=[Part(root=TextPart(text="test message"))],
                )
            )
            
            # Should timeout
            with pytest.raises(A2AClientError, match="Request timed out"):
                await asyncio.wait_for(
                    kafka_transport.send_message(request), 
                    timeout=0.1
                )

    @pytest.mark.asyncio
    @patch('a2a.client.transports.kafka.AIOKafkaProducer')
    @patch('a2a.client.transports.kafka.AIOKafkaConsumer')
    async def test_send_message_streaming(self, mock_consumer_class, mock_producer_class, kafka_transport):
        """Test streaming message sending."""
        # Mock producer and consumer
        mock_producer = AsyncMock()
        mock_consumer = AsyncMock()
        mock_producer_class.return_value = mock_producer
        mock_consumer_class.return_value = mock_consumer
        
        # Start transport
        await kafka_transport.start()
        
        # Mock correlation manager for streaming
        with patch.object(kafka_transport.correlation_manager, 'generate_correlation_id') as mock_gen_id, \
             patch.object(kafka_transport.correlation_manager, 'register_streaming') as mock_register:
            
            mock_gen_id.return_value = "test-correlation-id"
            
            # Create a streaming future that yields responses
            from a2a.client.transports.kafka_correlation import StreamingFuture
            streaming_future = StreamingFuture()
            mock_register.return_value = streaming_future
            
            # Send streaming message
            request = MessageSendParams(
                message=Message(
                    message_id="msg-1",
                    role=Role.user,
                    parts=[Part(root=TextPart(text="test message"))],
                )
            )
            
            # Start the streaming request
            stream = kafka_transport.send_message_streaming(request)
            
            # Simulate receiving responses
            response1 = Message(
                message_id="msg-2",
                role=Role.assistant,
                parts=[Part(root=TextPart(text="response 1"))],
            )
            response2 = Message(
                message_id="msg-3",
                role=Role.assistant,
                parts=[Part(root=TextPart(text="response 2"))],
            )
            
            # Put responses in the streaming future
            await streaming_future.put(response1)
            await streaming_future.put(response2)
            streaming_future.set_done()
            
            # Collect responses
            responses = []
            async for response in stream:
                responses.append(response)
                if len(responses) >= 2:  # Prevent infinite loop
                    break
            
            assert len(responses) == 2
            assert responses[0] == response1
            assert responses[1] == response2

    def test_sanitize_topic_name(self, kafka_transport):
        """Test topic name sanitization."""
        # Test normal name
        assert kafka_transport._sanitize_topic_name("test-agent") == "test-agent"
        
        # Test name with invalid characters
        assert kafka_transport._sanitize_topic_name("test@agent#123") == "test_agent_123"
        
        # Test empty name
        assert kafka_transport._sanitize_topic_name("") == "unknown_agent"
        
        # Test very long name
        long_name = "a" * 300
        sanitized = kafka_transport._sanitize_topic_name(long_name)
        assert len(sanitized) <= 200

    def test_create_classmethod(self, agent_card):
        """Test the create class method."""
        # Test with full URL
        transport = KafkaClientTransport.create(
            agent_card=agent_card,
            url="kafka://localhost:9092/custom-topic",
            config=None,
            interceptors=[]
        )
        assert transport.bootstrap_servers == "localhost:9092"
        assert transport.request_topic == "custom-topic"
        assert not transport._auto_start  # Should be False by default
        
        # Test with URL without topic (should use default)
        transport = KafkaClientTransport.create(
            agent_card=agent_card,
            url="kafka://localhost:9092",
            config=None,
            interceptors=[]
        )
        assert transport.bootstrap_servers == "localhost:9092"
        assert transport.request_topic == "a2a-requests"
        
        # Test invalid URL
        with pytest.raises(ValueError, match="Kafka URL must start with 'kafka://'"):
            KafkaClientTransport.create(
                agent_card=agent_card,
                url="http://localhost:9092",
                config=None,
                interceptors=[]
            )

@pytest.mark.integration
class TestKafkaIntegration:
    """Integration tests for Kafka transport (requires running Kafka)."""
    
    @pytest.mark.skip(reason="Requires running Kafka instance")
    @pytest.mark.asyncio
    async def test_real_kafka_connection(self, agent_card):
        """Test connection to real Kafka instance."""
        transport = KafkaClientTransport(
            agent_card=agent_card,
            bootstrap_servers="localhost:9092"
        )
        
        try:
            await transport.start()
            assert transport._running is True
        finally:
            await transport.stop()
            assert transport._running is False
