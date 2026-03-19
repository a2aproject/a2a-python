import httpx
import pytest

from fastapi import FastAPI

from a2a.server.agent_execution import AgentExecutor, RequestContext
from starlette.applications import Starlette
from a2a.server.routes import AgentCardRoutes, JsonRpcRoutes, RestRoutes
from a2a.server.events import EventQueue
from a2a.server.events.in_memory_queue_manager import InMemoryQueueManager
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks.inmemory_push_notification_config_store import (
    InMemoryPushNotificationConfigStore,
)
from a2a.server.tasks.inmemory_task_store import InMemoryTaskStore
from a2a.types.a2a_pb2 import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
)
from a2a.utils.constants import TransportProtocol


class DummyAgentExecutor(AgentExecutor):
    """An agent executor that does nothing for integration testing."""

    async def execute(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        pass

    async def cancel(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        pass


@pytest.mark.asyncio
async def test_agent_card_integration() -> None:
    """Tests that the agent card is correctly served via REST and JSONRPC."""
    # 1. Define AgentCard
    agent_card = AgentCard(
        name='Test Agent',
        description='An agent for testing agent card serving.',
        version='1.0.0',
        capabilities=AgentCapabilities(streaming=True, push_notifications=True),
        skills=[],
        default_input_modes=['text/plain'],
        default_output_modes=['text/plain'],
        supported_interfaces=[
            AgentInterface(
                protocol_binding=TransportProtocol.JSONRPC,
                url='http://localhost/jsonrpc/',
            ),
            AgentInterface(
                protocol_binding=TransportProtocol.HTTP_JSON,
                url='http://localhost/rest/',
            ),
        ],
    )

    # 2. Setup Server
    task_store = InMemoryTaskStore()
    handler = DefaultRequestHandler(
        agent_executor=DummyAgentExecutor(),
        task_store=task_store,
        queue_manager=InMemoryQueueManager(),
        push_config_store=InMemoryPushNotificationConfigStore(),
    )
    app = FastAPI()

    # Mount JSONRPC application
    jsonrpc_routes = [
        *AgentCardRoutes(
            agent_card=agent_card, card_url='/.well-known/agent-card.json'
        ).routes,
        *JsonRpcRoutes(
            agent_card=agent_card, request_handler=handler, rpc_url='/'
        ).routes,
    ]
    jsonrpc_app = Starlette(routes=jsonrpc_routes)
    app.mount('/jsonrpc', jsonrpc_app)

    # Mount REST application
    rest_routes = [
        *AgentCardRoutes(
            agent_card=agent_card, card_url='/.well-known/agent-card.json'
        ).routes,
        *RestRoutes(
            agent_card=agent_card, request_handler=handler, rpc_url=''
        ).routes,
    ]
    rest_app = Starlette(routes=rest_routes)
    app.mount('/rest', rest_app)

    expected_content = {
        'name': 'Test Agent',
        'description': 'An agent for testing agent card serving.',
        'supportedInterfaces': [
            {'url': 'http://localhost/jsonrpc/', 'protocolBinding': 'JSONRPC'},
            {'url': 'http://localhost/rest/', 'protocolBinding': 'HTTP+JSON'},
        ],
        'version': '1.0.0',
        'capabilities': {'streaming': True, 'pushNotifications': True},
        'defaultInputModes': ['text/plain'],
        'defaultOutputModes': ['text/plain'],
        'additionalInterfaces': [
            {'transport': 'HTTP+JSON', 'url': 'http://localhost/rest/'}
        ],
        'preferredTransport': 'JSONRPC',
        'protocolVersion': '0.3',
        'skills': [],
        'url': 'http://localhost/jsonrpc/',
    }

    # 3. Use direct http client (ASGITransport) to fetch and assert
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url='http://testserver'
    ) as client:
        # Fetch from JSONRPC endpoint
        resp_jsonrpc = await client.get('/jsonrpc/.well-known/agent-card.json')
        assert resp_jsonrpc.status_code == 200
        assert resp_jsonrpc.json() == expected_content

        # Fetch from REST endpoint
        resp_rest = await client.get('/rest/.well-known/agent-card.json')
        assert resp_rest.status_code == 200
        assert resp_rest.json() == expected_content
