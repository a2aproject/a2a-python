import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import httpx
from httpx import ASGITransport, AsyncClient

from a2a.types.a2a_pb2 import (
    AgentCard,
    AgentInterface,
    SendMessageRequest,
    Message,
    GetTaskRequest,
    AgentCapabilities,
    ListTasksRequest,
    ListTasksResponse,
    Task,
)
from a2a.client.transports import RestTransport, JsonRpcTransport, GrpcTransport
from a2a.client.transports.tenant_decorator import TenantTransportDecorator
from a2a.client import ClientConfig, ClientFactory
from a2a.utils.constants import TransportProtocol

from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from starlette.applications import Starlette
from a2a.server.request_handlers.request_handler import RequestHandler
from a2a.server.context import ServerCallContext


class TestTenantDecorator:
    @pytest.fixture
    def agent_card(self):
        return AgentCard(
            supported_interfaces=[
                AgentInterface(
                    url='http://example.com/rest',
                    protocol_binding=TransportProtocol.HTTP_JSON,
                    tenant='tenant-1',
                ),
                AgentInterface(
                    url='http://example.com/jsonrpc',
                    protocol_binding=TransportProtocol.JSONRPC,
                    tenant='tenant-2',
                ),
                AgentInterface(
                    url='http://example.com/grpc',
                    protocol_binding=TransportProtocol.GRPC,
                    tenant='tenant-3',
                ),
            ],
            capabilities=AgentCapabilities(streaming=True),
        )

    @pytest.mark.asyncio
    async def test_tenant_decorator_rest(self, agent_card):
        mock_httpx = AsyncMock(spec=httpx.AsyncClient)
        mock_httpx.build_request.return_value = MagicMock()
        mock_httpx.send.return_value = MagicMock(
            status_code=200, json=lambda: {'message': {}}
        )

        config = ClientConfig(
            httpx_client=mock_httpx,
            supported_protocol_bindings=[TransportProtocol.HTTP_JSON],
        )
        factory = ClientFactory(config)
        client = factory.create(agent_card)

        assert isinstance(client._transport, TenantTransportDecorator)
        assert client._transport._tenant == 'tenant-1'

        # Test SendMessage (POST) - Use transport directly to avoid streaming complexity in mock
        request = SendMessageRequest(message=Message(parts=[{'text': 'hi'}]))
        await client._transport.send_message(request)

        # Check that tenant was populated in request
        assert request.tenant == 'tenant-1'

        # Check that path was prepended in the underlying transport
        mock_httpx.build_request.assert_called()
        send_call = next(
            c
            for c in mock_httpx.build_request.call_args_list
            if 'message:send' in c.args[1]
        )
        args, kwargs = send_call
        assert args[1] == 'http://example.com/rest/tenant-1/message:send'
        assert 'tenant' in kwargs['json']

    @pytest.mark.asyncio
    async def test_tenant_decorator_jsonrpc(self, agent_card):
        mock_httpx = AsyncMock(spec=httpx.AsyncClient)
        mock_httpx.build_request.return_value = MagicMock()
        mock_httpx.send.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                'result': {'message': {}},
                'id': '1',
                'jsonrpc': '2.0',
            },
        )

        config = ClientConfig(
            httpx_client=mock_httpx,
            supported_protocol_bindings=[TransportProtocol.JSONRPC],
        )
        factory = ClientFactory(config)
        client = factory.create(agent_card)

        assert isinstance(client._transport, TenantTransportDecorator)
        assert client._transport._tenant == 'tenant-2'

        request = SendMessageRequest(message=Message(parts=[{'text': 'hi'}]))
        await client._transport.send_message(request)

        mock_httpx.build_request.assert_called()
        _, kwargs = mock_httpx.build_request.call_args
        assert kwargs['json']['params']['tenant'] == 'tenant-2'

    @pytest.mark.asyncio
    async def test_tenant_decorator_grpc(self, agent_card):
        mock_channel = MagicMock()
        config = ClientConfig(
            grpc_channel_factory=lambda url: mock_channel,
            supported_protocol_bindings=[TransportProtocol.GRPC],
        )

        with patch('a2a.types.a2a_pb2_grpc.A2AServiceStub') as mock_stub_class:
            mock_stub = mock_stub_class.return_value
            mock_stub.SendMessage = AsyncMock(return_value={'message': {}})

            factory = ClientFactory(config)
            client = factory.create(agent_card)

            assert isinstance(client._transport, TenantTransportDecorator)
            assert client._transport._tenant == 'tenant-3'

            await client._transport.send_message(
                SendMessageRequest(message=Message(parts=[{'text': 'hi'}]))
            )

            call_args = mock_stub.SendMessage.call_args
            assert call_args[0][0].tenant == 'tenant-3'

    @pytest.mark.asyncio
    async def test_tenant_decorator_explicit_override(self, agent_card):
        mock_httpx = AsyncMock(spec=httpx.AsyncClient)
        mock_httpx.build_request.return_value = MagicMock()
        mock_httpx.send.return_value = MagicMock(
            status_code=200, json=lambda: {'message': {}}
        )

        config = ClientConfig(
            httpx_client=mock_httpx,
            supported_protocol_bindings=[TransportProtocol.HTTP_JSON],
        )
        factory = ClientFactory(config)
        client = factory.create(agent_card)

        request = SendMessageRequest(
            message=Message(parts=[{'text': 'hi'}]), tenant='explicit-tenant'
        )
        await client._transport.send_message(request)

        assert request.tenant == 'explicit-tenant'

        send_call = next(
            c
            for c in mock_httpx.build_request.call_args_list
            if 'message:send' in c.args[1]
        )
        args, _ = send_call
        assert args[1] == 'http://example.com/rest/explicit-tenant/message:send'


class TestJSONRPCTenantIntegration:
    @pytest.fixture
    def mock_handler(self):
        handler = AsyncMock(spec=RequestHandler)
        handler.on_list_tasks.return_value = ListTasksResponse(
            tasks=[Task(id='task-1')]
        )
        return handler

    @pytest.fixture
    def jsonrpc_agent_card(self):
        return AgentCard(
            supported_interfaces=[
                AgentInterface(
                    url='http://testserver/jsonrpc',
                    protocol_binding=TransportProtocol.JSONRPC,
                    tenant='my-test-tenant',
                ),
            ],
            capabilities=AgentCapabilities(
                streaming=False,
                push_notifications=False,
            ),
        )

    @pytest.fixture
    def server_app(self, jsonrpc_agent_card, mock_handler):
        agent_card_routes = create_agent_card_routes(
            agent_card=jsonrpc_agent_card, card_url='/'
        )
        jsonrpc_routes = create_jsonrpc_routes(
            request_handler=mock_handler,
            rpc_url='/jsonrpc',
        )
        app = Starlette(routes=[*agent_card_routes, *jsonrpc_routes])
        return app

    @pytest.mark.asyncio
    async def test_jsonrpc_tenant_context_population(
        self, server_app, mock_handler, jsonrpc_agent_card
    ):
        """
        Integration test to verify that a tenant configured in the client
        is correctly propagated to the ServerCallContext in the server
        via the JSON-RPC transport.
        """
        # 1. Setup the client using the server app as the transport
        # We use ASGITransport so httpx calls go directly to the Starlette app
        transport = ASGITransport(app=server_app)
        async with AsyncClient(
            transport=transport, base_url='http://testserver'
        ) as httpx_client:
            # Create the A2A client properly configured
            config = ClientConfig(
                httpx_client=httpx_client,
                supported_protocol_bindings=[TransportProtocol.JSONRPC],
            )
            factory = ClientFactory(config)
            client = factory.create(jsonrpc_agent_card)

            # 2. Make the call (list_tasks)
            response = await client.list_tasks(ListTasksRequest())

            # 3. Verify response
            assert len(response.tasks) == 1
            assert response.tasks[0].id == 'task-1'

            # 4. Verify ServerCallContext on the server side
            mock_handler.on_list_tasks.assert_called_once()
            call_args = mock_handler.on_list_tasks.call_args

            # call_args[0] are positional args: (request, context)
            # Check call_args signature in jsonrpc_handler.py: await self.handler.list_tasks(request_obj, context)

            server_context = call_args[0][1]
            assert isinstance(server_context, ServerCallContext)
            assert server_context.tenant == 'my-test-tenant'
