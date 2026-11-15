"""Test that streaming SSE responses clean up without athrow() errors.

Reproduces https://github.com/a2aproject/a2a-python/issues/911 —
``RuntimeError: athrow(): asynchronous generator is already running``
during event-loop shutdown after consuming a streaming response.
"""

import asyncio
import gc
import json

from collections.abc import AsyncGenerator, AsyncIterator
from typing import Any, cast
from uuid import uuid4

import httpx
import pytest

from a2a.client.base_client import BaseClient
from a2a.client.client import ClientConfig
from a2a.client.client_factory import ClientFactory
from a2a.client.interceptors import AfterArgs, BeforeArgs, ClientCallInterceptor
from a2a.plugins.base import ClientPlugin
from a2a.plugins.manager import SequentialPluginManager
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
    StreamResponse,
    SubscribeToTaskRequest,
)
from a2a.utils import TransportProtocol
from starlette.applications import Starlette


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


class _OpenResponseStream(httpx.AsyncByteStream):
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.closed = False
        self.waiting = asyncio.Event()

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield self.payload
        self.waiting.set()
        await asyncio.Event().wait()

    async def aclose(self) -> None:
        self.closed = True


class _RejectResponse(ClientCallInterceptor):
    async def before(self, args: BeforeArgs) -> None:
        pass

    async def after(self, args: AfterArgs) -> None:
        raise RuntimeError('response rejected')


@pytest.mark.asyncio
@pytest.mark.parametrize('binding', ['JSONRPC', 'HTTP+JSON'])
@pytest.mark.parametrize('tenant', ['', 'tenant'])
@pytest.mark.parametrize('plugins_enabled', [False, True])
@pytest.mark.parametrize(
    'ending',
    [
        'close_send',
        'close_subscribe',
        'message',
        'interceptor_error',
        'cancel_read',
    ],
)
async def test_http_stream_is_closed_before_call_returns(
    binding: str,
    tenant: str,
    plugins_enabled: bool,
    ending: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event: dict[str, Any] = {
        'task': {
            'id': 'task',
            'contextId': 'context',
            'status': {'state': 'TASK_STATE_WORKING'},
        }
    }
    if ending == 'message':
        event = {
            'message': {
                'messageId': 'reply',
                'role': 'ROLE_AGENT',
                'parts': [{'text': 'done'}],
            }
        }
    if binding == 'JSONRPC':
        event = {'jsonrpc': '2.0', 'id': 'stream', 'result': event}
    response_stream = _OpenResponseStream(
        f'data: {json.dumps(event)}\n\n'.encode()
    )

    def respond(request: httpx.Request) -> httpx.Response:
        if binding == 'JSONRPC':
            payload = json.loads(request.content)
            event['id'] = payload['id']
            response_stream.payload = f'data: {json.dumps(event)}\n\n'.encode()
        return httpx.Response(
            200,
            headers={'Content-Type': 'text/event-stream'},
            stream=response_stream,
        )

    plugins = SequentialPluginManager(
        *([ClientPlugin()] if plugins_enabled else [])
    )
    monkeypatch.setattr('a2a.client.base_client.get_global', lambda: plugins)
    card = AgentCard(
        name='streaming agent',
        description='Test a live streaming response',
        version='1',
        default_input_modes=['text/plain'],
        default_output_modes=['text/plain'],
        capabilities=AgentCapabilities(streaming=True),
        supported_interfaces=[
            AgentInterface(
                url='http://agent',
                protocol_binding=binding,
                protocol_version='1.0',
                tenant=tenant,
            )
        ],
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond)
    ) as http:
        client = ClientFactory(
            ClientConfig(
                httpx_client=http, supported_protocol_bindings=[binding]
            )
        ).create(
            card, [_RejectResponse()] if ending == 'interceptor_error' else []
        )
        events = cast(
            'AsyncGenerator[StreamResponse]',
            client.subscribe(SubscribeToTaskRequest(id='task'))
            if ending == 'close_subscribe'
            else client.send_message(
                SendMessageRequest(
                    message=Message(
                        message_id='request',
                        role=Role.ROLE_USER,
                        parts=[Part(text='hi')],
                    )
                )
            ),
        )
        try:
            if ending == 'interceptor_error':
                with pytest.raises(RuntimeError, match='response rejected'):
                    await anext(events)
            elif ending == 'message':
                replies = [event async for event in events]
                assert len(replies) == 1
                assert replies[0].message.parts[0].text == 'done'
            else:
                first = await anext(events)
                assert first.task.id == 'task'
                assert not response_stream.closed
                if ending == 'cancel_read':
                    read = asyncio.create_task(anext(events))
                    await asyncio.wait_for(response_stream.waiting.wait(), 1)
                    read.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await read
                else:
                    await events.aclose()
            assert response_stream.closed
            assert not http.is_closed
        finally:
            await events.aclose()
