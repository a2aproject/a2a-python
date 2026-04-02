"""Test that streaming SSE responses clean up without athrow() errors.

Reproduces https://github.com/a2aproject/a2a-python/issues/911 —
``RuntimeError: athrow(): asynchronous generator is already running``
during event-loop shutdown after consuming a streaming response.
"""

import asyncio
import gc

from typing import Any
from uuid import uuid4

import httpx
import pytest

from starlette.applications import Starlette

from a2a.client.base_client import BaseClient
from a2a.client.client import ClientConfig
from a2a.client.client_factory import ClientFactory
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.events.in_memory_queue_manager import InMemoryQueueManager
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks.inmemory_task_store import InMemoryTaskStore
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    Message,
    Part,
    Role,
    SendMessageRequest,
)
from a2a.utils import TransportProtocol


class _MessageExecutor(AgentExecutor):
    """Responds with a single Message event."""

    async def execute(self, ctx: RequestContext, eq: EventQueue) -> None:
        await eq.enqueue_event(
            Message(
                role=Role.ROLE_AGENT,
                message_id=str(uuid4()),
                parts=[Part(text='Hello')],
                context_id=ctx.context_id,
                task_id=ctx.task_id,
            )
        )

    async def cancel(self, ctx: RequestContext, eq: EventQueue) -> None:
        pass


@pytest.fixture
def client():
    """Creates a JSON-RPC client backed by an in-process ASGI server."""
    card = AgentCard(
        name='T',
        description='T',
        version='1',
        capabilities=AgentCapabilities(streaming=True),
        default_input_modes=['text/plain'],
        default_output_modes=['text/plain'],
        supported_interfaces=[
            AgentInterface(
                protocol_binding=TransportProtocol.JSONRPC,
                url='http://test',
            ),
        ],
    )
    handler = DefaultRequestHandler(
        agent_executor=_MessageExecutor(),
        task_store=InMemoryTaskStore(),
        agent_card=card,
        queue_manager=InMemoryQueueManager(),
    )
    app = Starlette(
        routes=[
            *create_agent_card_routes(agent_card=card, card_url='/card'),
            *create_jsonrpc_routes(
                request_handler=handler,
                rpc_url='/',
            ),
        ]
    )
    return ClientFactory(
        config=ClientConfig(
            httpx_client=httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url='http://test',
            )
        )
    ).create(card)


@pytest.mark.asyncio
async def test_stream_message_no_athrow(client: BaseClient) -> None:
    """Consuming a streamed Message must not leave broken async generators."""
    errors: list[dict[str, Any]] = []
    loop = asyncio.get_event_loop()
    orig = loop.get_exception_handler()
    loop.set_exception_handler(lambda _l, ctx: errors.append(ctx))

    try:
        msg = Message(
            role=Role.ROLE_USER,
            message_id=f'msg-{uuid4()}',
            parts=[Part(text='hi')],
        )
        events = [
            e
            async for e in client.send_message(
                request=SendMessageRequest(message=msg)
            )
        ]
        assert events
        assert events[0].HasField('message')

        gc.collect()
        await loop.shutdown_asyncgens()

        bad = [
            e
            for e in errors
            if 'asynchronous generator' in str(e.get('message', ''))
        ]
        assert not bad, '\n'.join(str(e.get('message', '')) for e in bad)
    finally:
        loop.set_exception_handler(orig)
        await client.close()
