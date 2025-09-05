import pytest
from unittest.mock import AsyncMock, patch

from a2a.types import TaskStatusUpdateEvent
from a2a.utils.stream_write.redis_stream_writer import RedisStreamInjector


@pytest.fixture
def mock_redis_client():
    """Fixture providing a mock Redis client."""
    client = AsyncMock()
    client.xadd = AsyncMock(return_value='123-0')
    client.ping = AsyncMock()
    client.aclose = AsyncMock()
    return client


class TestRedisStreamInjector:
    """Test suite for RedisStreamInjector."""

    def test_init_without_redis_import_raises_error(self):
        """Test that initialization fails when redis is not available."""
        with patch('a2a.utils.stream_write.redis_stream_writer.Redis', None):
            with pytest.raises(ImportError, match='redis package is required'):
                RedisStreamInjector()

    def test_init_with_redis_available(self):
        """Test successful initialization when redis is available."""
        with patch(
            'a2a.utils.stream_write.redis_stream_writer.Redis'
        ) as mock_redis:
            injector = RedisStreamInjector('redis://localhost:6379/0')
            assert injector.redis_url == 'redis://localhost:6379/0'
            assert injector._client is None
            assert not injector._connected

    @pytest.mark.asyncio
    async def test_connect_success(self):
        """Test successful connection to Redis."""
        mock_client = AsyncMock()
        mock_client.ping = AsyncMock()

        with patch(
            'a2a.utils.stream_write.redis_stream_writer.Redis'
        ) as mock_redis_class:
            mock_redis_class.from_url.return_value = mock_client

            injector = RedisStreamInjector()
            await injector.connect()

            assert injector._client == mock_client
            assert injector._connected
            mock_redis_class.from_url.assert_called_once_with(
                'redis://localhost:6379/0'
            )
            mock_client.ping.assert_called_once()

    @pytest.mark.asyncio
    async def test_connect_already_connected(self):
        """Test that connect does nothing if already connected."""
        mock_client = AsyncMock()

        with patch(
            'a2a.utils.stream_write.redis_stream_writer.Redis'
        ) as mock_redis_class:
            mock_redis_class.from_url.return_value = mock_client

            injector = RedisStreamInjector()
            injector._connected = True
            injector._client = mock_client

            await injector.connect()

            # Should not create new client or ping
            mock_redis_class.from_url.assert_not_called()
            mock_client.ping.assert_not_called()

    @pytest.mark.asyncio
    async def test_connect_failure(self):
        """Test connection failure."""
        with patch(
            'a2a.utils.stream_write.redis_stream_writer.Redis'
        ) as mock_redis_class:
            mock_redis_class.from_url.side_effect = Exception(
                'Connection failed'
            )

            injector = RedisStreamInjector()

            with pytest.raises(Exception, match='Connection failed'):
                await injector.connect()

            assert not injector._connected
            assert injector._client is None

    @pytest.mark.asyncio
    async def test_disconnect(self):
        """Test disconnecting from Redis."""
        mock_client = AsyncMock()
        mock_client.aclose = AsyncMock()

        injector = RedisStreamInjector(redis_client=mock_client)
        injector._connected = True

        await injector.disconnect()

        assert not injector._connected
        assert injector._client is None
        mock_client.aclose.assert_called_once()

    @pytest.mark.asyncio
    async def test_disconnect_not_connected(self):
        """Test disconnect when not connected."""
        mock_client = AsyncMock()
        injector = RedisStreamInjector(redis_client=mock_client)
        injector._connected = False

        await injector.disconnect()

        # Should not call aclose since not connected
        mock_client.aclose.assert_not_called()

        # Should not raise any errors and client should remain
        assert not injector._connected
        assert injector._client == mock_client

    @pytest.mark.asyncio
    async def test_context_manager(self):
        """Test async context manager."""
        mock_client = AsyncMock()
        mock_client.ping = AsyncMock()
        mock_client.aclose = AsyncMock()

        injector = RedisStreamInjector(redis_client=mock_client)

        async with injector as ctx_injector:
            assert ctx_injector == injector
            assert injector._connected

        assert not injector._connected
        mock_client.aclose.assert_called_once()

    def test_get_stream_key(self):
        """Test stream key generation."""
        mock_client = AsyncMock()
        injector = RedisStreamInjector(redis_client=mock_client)

        key = injector._get_stream_key('test_task')
        assert key == 'a2a:task:test_task'

    def test_get_stream_key_empty_task_id(self):
        """Test stream key generation with empty task_id."""
        mock_client = AsyncMock()
        injector = RedisStreamInjector(redis_client=mock_client)

        with pytest.raises(ValueError, match='task_id cannot be empty'):
            injector._get_stream_key('')

    def test_serialize_event(self):
        """Test event serialization."""
        injector = RedisStreamInjector(redis_client=AsyncMock())

        event_data = injector._serialize_event('test_type', {'key': 'value'})
        assert event_data['type'] == 'test_type'
        assert 'payload' in event_data

    @pytest.mark.asyncio
    async def test_append_to_stream(self):
        """Test appending event to stream."""
        mock_client = AsyncMock()
        mock_client.xadd = AsyncMock(return_value='123-0')

        injector = RedisStreamInjector(redis_client=mock_client)

        event_data = {'type': 'Test', 'payload': '{"data": "test"}'}
        result = await injector._append_to_stream('test_task', event_data)

        assert result == '123-0'
        mock_client.xadd.assert_called_once_with(
            'a2a:task:test_task', event_data
        )

    @pytest.mark.asyncio
    async def test_append_to_stream_not_connected(self):
        """Test append_to_stream when not connected."""
        mock_client = AsyncMock()
        injector = RedisStreamInjector(redis_client=mock_client)
        injector._connected = False

        with pytest.raises(RuntimeError, match='Not connected to Redis'):
            await injector._append_to_stream('test_task', {})

    @pytest.mark.asyncio
    async def test_stream_message_with_dict(self):
        """Test streaming message with dict input."""
        mock_client = AsyncMock()
        mock_client.xadd = AsyncMock(return_value='123-0')

        injector = RedisStreamInjector(redis_client=mock_client)

        message_data = {'content': 'test message', 'role': 'assistant'}
        result = await injector.stream_message(
            'ctx123', 'task123', message_data
        )

        assert result == '123-0'
        mock_client.xadd.assert_called_once()

        # Verify the call arguments
        call_args = mock_client.xadd.call_args
        stream_key = call_args[0][0]
        event_data = call_args[0][1]

        assert stream_key == 'a2a:task:task123'
        assert event_data['type'] == 'Message'
        assert 'payload' in event_data

    @pytest.mark.asyncio
    async def test_stream_message_with_message_object(self):
        """Test streaming message with Message object."""
        mock_client = AsyncMock()
        mock_client.xadd = AsyncMock(return_value='123-0')

        injector = RedisStreamInjector()
        injector._client = mock_client
        injector._connected = True

        # Create a proper Message object with required fields
        from a2a.types import Message, TextPart, Role

        message = Message(
            message_id='msg-123',
            parts=[TextPart(text='test message')],
            role=Role.agent,
        )
        result = await injector.stream_message('ctx123', 'task123', message)

        assert result == '123-0'
        mock_client.xadd.assert_called_once()

    @pytest.mark.asyncio
    async def test_stream_message_empty_task_id(self):
        """Test stream_message with empty task_id."""
        injector = RedisStreamInjector()

        with pytest.raises(ValueError, match='task_id cannot be empty'):
            await injector.stream_message('ctx123', '', {'content': 'test'})

    @pytest.mark.asyncio
    async def test_stream_message_empty_context_id(self):
        """Test stream_message with empty context_id."""
        injector = RedisStreamInjector()

        with pytest.raises(ValueError, match='context_id cannot be empty'):
            await injector.stream_message('', 'task123', {'content': 'test'})

    @pytest.mark.asyncio
    async def test_update_status_with_task_status_update_event(self):
        """Test update_status with TaskStatusUpdateEvent."""
        mock_client = AsyncMock()
        mock_client.xadd = AsyncMock(return_value='123-0')

        injector = RedisStreamInjector()
        injector._client = mock_client
        injector._connected = True

        # Create a TaskStatusUpdateEvent
        status_event = TaskStatusUpdateEvent(
            context_id='ctx123',
            task_id='task123',
            final=False,
            status={
                'state': 'working',
                'message': None,
                'timestamp': '2023-01-01T00:00:00Z',
            },
        )

        result = await injector.update_status('ctx123', 'task123', status_event)

        assert result == '123-0'
        mock_client.xadd.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_status_with_dict(self):
        """Test update_status with dict status."""
        mock_client = AsyncMock()
        mock_client.xadd = AsyncMock(return_value='123-0')

        injector = RedisStreamInjector()
        injector._client = mock_client
        injector._connected = True

        result = await injector.update_status(
            'ctx123', 'task123', {'state': 'completed'}
        )

        assert result == '123-0'
        mock_client.xadd.assert_called_once()

    @pytest.mark.asyncio
    async def test_final_message(self):
        """Test final_message method."""
        mock_client = AsyncMock()
        mock_client.xadd = AsyncMock(return_value='123-0')

        injector = RedisStreamInjector()
        injector._client = mock_client
        injector._connected = True

        message_data = {'content': 'final message', 'role': 'assistant'}
        result = await injector.final_message('ctx123', 'task123', message_data)

        assert result == '123-0'
        # Should call xadd twice: once for message, once for status update
        assert mock_client.xadd.call_count == 2

    @pytest.mark.asyncio
    async def test_append_raw(self):
        """Test append_raw method."""
        mock_client = AsyncMock()
        mock_client.xadd = AsyncMock(return_value='123-0')

        injector = RedisStreamInjector()
        injector._client = mock_client
        injector._connected = True

        result = await injector.append_raw(
            'task123', 'CustomEvent', '{"data": "test"}'
        )

        assert result == '123-0'
        mock_client.xadd.assert_called_once_with(
            'a2a:task:task123',
            {'type': 'CustomEvent', 'payload': '{"data": "test"}'},
        )

    @pytest.mark.asyncio
    async def test_get_latest_event(self):
        """Test get_latest_event method."""
        mock_client = AsyncMock()
        mock_client.xrevrange = AsyncMock(
            return_value=[
                ('123-0', {'type': 'Message', 'payload': '{"data": "test"}'})
            ]
        )

        injector = RedisStreamInjector()
        injector._client = mock_client
        injector._connected = True

        result = await injector.get_latest_event('task123')

        assert result == {
            'id': '123-0',
            'type': 'Message',
            'payload': '{"data": "test"}',
        }
        mock_client.xrevrange.assert_called_once_with(
            'a2a:task:task123', '+', '-', count=1
        )

    @pytest.mark.asyncio
    async def test_get_latest_event_no_events(self):
        """Test get_latest_event when no events exist."""
        mock_client = AsyncMock()
        mock_client.xrevrange = AsyncMock(return_value=[])

        injector = RedisStreamInjector()
        injector._client = mock_client
        injector._connected = True

        result = await injector.get_latest_event('task123')

        assert result is None

    @pytest.mark.asyncio
    async def test_get_latest_event_exception(self):
        """Test get_latest_event when exception occurs."""
        mock_client = AsyncMock()
        mock_client.xrevrange = AsyncMock(side_effect=Exception('Redis error'))

        injector = RedisStreamInjector()
        injector._client = mock_client
        injector._connected = True

        result = await injector.get_latest_event('task123')

        assert result is None

    @pytest.mark.asyncio
    async def test_get_events_since(self):
        """Test get_events_since method."""
        mock_client = AsyncMock()
        mock_client.xrange = AsyncMock(
            return_value=[
                ('123-0', {'type': 'Message', 'payload': '{"data": "test1"}'}),
                ('124-0', {'type': 'Status', 'payload': '{"data": "test2"}'}),
            ]
        )

        injector = RedisStreamInjector()
        injector._client = mock_client
        injector._connected = True

        result = await injector.get_events_since('task123', '122-0')

        expected = [
            {'id': '123-0', 'type': 'Message', 'payload': '{"data": "test1"}'},
            {'id': '124-0', 'type': 'Status', 'payload': '{"data": "test2"}'},
        ]
        assert result == expected
        mock_client.xrange.assert_called_once_with(
            'a2a:task:task123', '122-0', '+'
        )

    @pytest.mark.asyncio
    async def test_get_events_since_exception(self):
        """Test get_events_since when exception occurs."""
        mock_client = AsyncMock()
        mock_client.xrange = AsyncMock(side_effect=Exception('Redis error'))

        injector = RedisStreamInjector()
        injector._client = mock_client
        injector._connected = True

        result = await injector.get_events_since('task123')

        assert result == []
