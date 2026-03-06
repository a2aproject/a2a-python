import pytest
from unittest.mock import AsyncMock, MagicMock
from httpx import ASGITransport, AsyncClient

from a2a.client import ClientFactory, ClientConfig
from a2a.server.apps.jsonrpc.starlette_app import A2AStarletteApplication
from a2a.server.request_handlers.request_handler import RequestHandler
from a2a.types.a2a_pb2 import (
    AgentCard,
    AgentInterface,
    AgentCapabilities,
    ListTasksRequest,
    ListTasksResponse,
    Task,
)
from a2a.server.context import ServerCallContext
from a2a.utils.constants import TransportProtocol


@pytest.fixture
def mock_handler():
    handler = AsyncMock(spec=RequestHandler)
    handler.on_list_tasks.return_value = ListTasksResponse(
        tasks=[Task(id='task-1')]
    )
    return handler


@pytest.fixture
def agent_card():
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
def server_app(agent_card, mock_handler):
    app = A2AStarletteApplication(
        agent_card=agent_card,
        http_handler=mock_handler,
    ).build(rpc_url='/jsonrpc')
    return app


@pytest.mark.asyncio
async def test_jsonrpc_tenant_context_population(
    server_app, mock_handler, agent_card
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
        client = factory.create(agent_card)

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
