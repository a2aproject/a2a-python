from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock

import pytest

from a2a.client.transports.base import ClientTransport
from a2a.client.transports.tenant_decorator import TenantTransportDecorator
from a2a.types.a2a_pb2 import (
    CancelTaskRequest,
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
    TaskPushNotificationConfig,
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
        self, mock_transport: AsyncMock, method_name: str, request_obj: Any
    ) -> None:
        """Test that tenant is set on the request for all methods."""
        tenant_id = 'test-tenant'
        decorator = TenantTransportDecorator(mock_transport, tenant_id)
        mock_method = getattr(mock_transport, method_name)

        original = request_obj.SerializeToString()
        await getattr(decorator, method_name)(request_obj)
        other = TenantTransportDecorator(mock_transport, 'other-tenant')
        await getattr(other, method_name)(request_obj)

        assert mock_method.await_count == 2
        assert mock_transport.mock_calls[0][0] == method_name
        assert [
            call.args[0].tenant for call in mock_method.await_args_list
        ] == [
            tenant_id,
            'other-tenant',
        ]
        assert request_obj.SerializeToString() == original

    @pytest.mark.asyncio
    async def test_streaming_methods(self, mock_transport: AsyncMock) -> None:
        """Test that tenant is set on the request for streaming methods."""
        tenant_id = 'test-tenant'
        decorator = TenantTransportDecorator(mock_transport, tenant_id)

        tenants: list[str] = []

        async def mock_stream(
            request: Any, **kwargs: Any
        ) -> AsyncGenerator[StreamResponse]:
            tenants.append(request.tenant)
            yield StreamResponse()

        mock_transport.subscribe.side_effect = mock_stream
        mock_transport.send_message_streaming.side_effect = mock_stream
        other = TenantTransportDecorator(mock_transport, 'other-tenant')
        for method, request in [
            ('subscribe', SubscribeToTaskRequest(id='t1')),
            ('send_message_streaming', SendMessageRequest()),
        ]:
            original = request.SerializeToString()
            async for _ in getattr(decorator, method)(request):
                pass
            async for _ in getattr(other, method)(request):
                pass
            assert request.SerializeToString() == original
        assert tenants == [tenant_id, 'other-tenant', tenant_id, 'other-tenant']
