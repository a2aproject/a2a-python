import pytest
from unittest.mock import AsyncMock, MagicMock

from a2a.client.transports.base import ClientTransport
from a2a.client.transports.tenant_decorator import TenantTransportDecorator
from a2a.types.a2a_pb2 import (
    AgentCard,
    CancelTaskRequest,
    TaskPushNotificationConfig,
    DeleteTaskPushNotificationConfigRequest,
    GetExtendedAgentCardRequest,
    GetTaskPushNotificationConfigRequest,
    GetTaskRequest,
    ListTaskPushNotificationConfigsRequest,
    ListTasksRequest,
    Message,
    Part,
    SendMessageRequest,
    StreamResponse,
    SubscribeToTaskRequest,
)


@pytest.fixture
def mock_transport() -> AsyncMock:
    return AsyncMock(spec=ClientTransport)


class TestTenantTransportDecorator:
    @pytest.mark.asyncio
    async def test_resolve_tenant_logic(
        self, mock_transport: AsyncMock
    ) -> None:
        tenant_id = 'test-tenant'
        decorator = TenantTransportDecorator(mock_transport, tenant_id)

        # Case 1: Tenant already set on request
        assert decorator._resolve_tenant('existing-tenant') == 'existing-tenant'

        # Case 2: Tenant not set (empty string)
        assert decorator._resolve_tenant('') == tenant_id

    @pytest.mark.asyncio
    async def test_resolve_tenant_logic_empty_tenant(
        self, mock_transport: AsyncMock
    ) -> None:
        decorator = TenantTransportDecorator(mock_transport, '')

        # Case 1: Tenant already set on request
        assert decorator._resolve_tenant('existing-tenant') == 'existing-tenant'

        # Case 2: Tenant not set (empty string)
        assert decorator._resolve_tenant('') == ''

    @pytest.mark.parametrize(
        'method_name, request_obj',
        [
            (
                'send_message',
                SendMessageRequest(message=Message(parts=[Part(text='hello')])),
            ),
            (
                'get_task',
                GetTaskRequest(id='t1'),
            ),
            (
                'list_tasks',
                ListTasksRequest(),
            ),
            (
                'cancel_task',
                CancelTaskRequest(id='t1'),
            ),
            (
                'create_task_push_notification_config',
                TaskPushNotificationConfig(task_id='t1'),
            ),
            (
                'get_task_push_notification_config',
                GetTaskPushNotificationConfigRequest(task_id='t1', id='c1'),
            ),
            (
                'list_task_push_notification_configs',
                ListTaskPushNotificationConfigsRequest(task_id='t1'),
            ),
            (
                'delete_task_push_notification_config',
                DeleteTaskPushNotificationConfigRequest(task_id='t1', id='c1'),
            ),
            ('get_extended_agent_card', GetExtendedAgentCardRequest()),
        ],
    )
    @pytest.mark.asyncio
    async def test_methods(
        self, mock_transport: AsyncMock, method_name, request_obj
    ) -> None:
        """Test that tenant is set on the request for all methods."""
        tenant_id = 'test-tenant'
        decorator = TenantTransportDecorator(mock_transport, tenant_id)
        mock_method = getattr(mock_transport, method_name)

        await getattr(decorator, method_name)(request_obj)

        mock_method.assert_called_once()
        assert mock_transport.mock_calls[0][0] == method_name
        assert request_obj.tenant == tenant_id

    @pytest.mark.asyncio
    async def test_streaming_methods(self, mock_transport: AsyncMock) -> None:
        """Test that tenant is set on the request for streaming methods."""
        tenant_id = 'test-tenant'
        decorator = TenantTransportDecorator(mock_transport, tenant_id)

        async def mock_stream(*args, **kwargs):
            yield StreamResponse()

        # Test subscribe
        mock_transport.subscribe.return_value = mock_stream()
        request_sub = SubscribeToTaskRequest(id='t1')
        async for _ in decorator.subscribe(request_sub):
            pass
        assert request_sub.tenant == tenant_id

        # Test send_message_streaming
        mock_transport.send_message_streaming.return_value = mock_stream()
        request_msg = SendMessageRequest()
        async for _ in decorator.send_message_streaming(request_msg):
            pass
        assert request_msg.tenant == tenant_id
