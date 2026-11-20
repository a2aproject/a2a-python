"""Test that streaming SSE responses clean up without athrow() errors.

Reproduces https://github.com/a2aproject/a2a-python/issues/911 —
``RuntimeError: athrow(): asynchronous generator is already running``
during event-loop shutdown after consuming a streaming response.
"""

import asyncio
import gc
import json

from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, cast
from uuid import uuid4

import pytest

from a2a.client.base_client import BaseClient
from a2a.client.client import ClientConfig
from a2a.client.client_factory import ClientFactory
from a2a.client.interceptors import AfterArgs, BeforeArgs, ClientCallInterceptor
from a2a.compat.v0_3 import proto_utils, types as types_v03
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
from a2a.utils._httpx_compat import httpx_lib
from google.protobuf.json_format import MessageToDict
from starlette.applications import Starlette


pytestmark = [pytest.mark.timeout(10), pytest.mark.httpx2]


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
            httpx_client=httpx_lib.AsyncClient(
                transport=httpx_lib.ASGITransport(app=app),
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


class _OpenResponseStream(httpx_lib.AsyncByteStream):
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


@asynccontextmanager
async def _streaming_client(
    binding: str,
    version: str,
    monkeypatch: pytest.MonkeyPatch,
    *,
    tenant: str = '',
    plugins_enabled: bool = False,
    message_reply: bool = False,
    reject_response: bool = False,
    subscribe_fallback: bool = False,
) -> AsyncIterator[
    tuple[BaseClient, httpx_lib.AsyncClient, _OpenResponseStream]
]:
    legacy = version == '0.3'
    task = {
        'id': 'task',
        'contextId': 'context',
        'status': {'state': 'working' if legacy else 'TASK_STATE_WORKING'},
    }
    message = {
        'messageId': 'reply',
        'role': 'agent' if legacy else 'ROLE_AGENT',
        'parts': [{'kind': 'text', 'text': 'done'}]
        if legacy
        else [{'text': 'done'}],
    }
    event: dict[str, Any] = (
        {'message': message} if message_reply else {'task': task}
    )
    if legacy and binding == 'JSONRPC':
        event = (
            dict(message, kind='message')
            if message_reply
            else dict(task, kind='task')
        )
    elif legacy:
        event = (
            {
                'message': MessageToDict(
                    proto_utils.ToProto.message(
                        types_v03.Message.model_validate(message)
                    )
                )
            }
            if message_reply
            else {
                'task': MessageToDict(
                    proto_utils.ToProto.task(
                        types_v03.Task.model_validate(dict(task, kind='task'))
                    )
                )
            }
        )
    response_stream = _OpenResponseStream(b'')
    methods: list[str] = []

    def respond(request: httpx_lib.Request) -> httpx_lib.Response:
        methods.append(request.method)
        if subscribe_fallback and request.method == 'POST':
            return httpx_lib.Response(405)
        data = event
        if binding == 'JSONRPC':
            data = {
                'jsonrpc': '2.0',
                'id': json.loads(request.content)['id'],
                'result': event,
            }
        response_stream.payload = f'data: {json.dumps(data)}\n\n'.encode()
        return httpx_lib.Response(
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
                protocol_version=version,
                tenant=tenant,
            )
        ],
    )
    async with httpx_lib.AsyncClient(
        transport=httpx_lib.MockTransport(respond)
    ) as http:
        client = ClientFactory(
            ClientConfig(
                httpx_client=http, supported_protocol_bindings=[binding]
            )
        ).create(card, [_RejectResponse()] if reject_response else [])
        yield client, http, response_stream
        assert response_stream.closed
        assert not http.is_closed
        if subscribe_fallback:
            assert methods == ['POST', 'GET']


def _send(client: BaseClient) -> AsyncGenerator[StreamResponse]:
    return cast(
        'AsyncGenerator[StreamResponse]',
        client.send_message(
            SendMessageRequest(
                message=Message(
                    message_id='request',
                    role=Role.ROLE_USER,
                    parts=[Part(text='hi')],
                )
            )
        ),
    )


@asynccontextmanager
async def _bounded_events(
    events: AsyncGenerator[StreamResponse],
) -> AsyncIterator[AsyncGenerator[StreamResponse]]:
    try:
        yield events
    finally:
        await asyncio.wait_for(events.aclose(), timeout=2)


@pytest.fixture(params=['JSONRPC', 'HTTP+JSON'])
def binding(request: pytest.FixtureRequest) -> str:
    return str(request.param)


@pytest.fixture(params=['1.0', '0.3'])
def version(request: pytest.FixtureRequest) -> str:
    return str(request.param)


@pytest.mark.asyncio
async def test_http_send_stream_closes(
    binding: str,
    version: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with _streaming_client(binding, version, monkeypatch) as (
        client,
        _,
        response,
    ):
        async with _bounded_events(_send(client)) as events:
            assert (
                await asyncio.wait_for(anext(events), timeout=2)
            ).task.id == 'task'
            assert not response.closed
        assert response.closed


@pytest.mark.asyncio
async def test_http_subscribe_stream_closes(
    binding: str,
    version: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with _streaming_client(binding, version, monkeypatch) as (
        client,
        _,
        response,
    ):
        events = cast(
            'AsyncGenerator[StreamResponse]',
            client.subscribe(SubscribeToTaskRequest(id='task')),
        )
        async with _bounded_events(events):
            assert (
                await asyncio.wait_for(anext(events), timeout=2)
            ).task.id == 'task'
            assert not response.closed
        assert response.closed


@pytest.mark.asyncio
async def test_http_message_reply_closes(
    binding: str,
    version: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with _streaming_client(
        binding, version, monkeypatch, message_reply=True
    ) as (client, _, response):
        async with _bounded_events(_send(client)) as events:
            reply = await asyncio.wait_for(anext(events), timeout=2)
            assert reply.message.parts[0].text == 'done'
            with pytest.raises(StopAsyncIteration):
                await asyncio.wait_for(anext(events), timeout=2)
            assert response.closed


@pytest.mark.asyncio
async def test_http_interceptor_error_closes(
    binding: str,
    version: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with _streaming_client(
        binding, version, monkeypatch, reject_response=True
    ) as (client, _, response):
        async with _bounded_events(_send(client)) as events:
            with pytest.raises(RuntimeError, match='response rejected'):
                await asyncio.wait_for(anext(events), timeout=2)
            assert response.closed


@pytest.mark.asyncio
async def test_http_cancelled_read_closes(
    binding: str,
    version: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with _streaming_client(binding, version, monkeypatch) as (
        client,
        _,
        response,
    ):
        async with _bounded_events(_send(client)) as events:
            assert (
                await asyncio.wait_for(anext(events), timeout=2)
            ).task.id == 'task'
            read = asyncio.create_task(anext(events))
            try:
                await asyncio.wait_for(response.waiting.wait(), timeout=2)
            finally:
                read.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(read, timeout=2)
            assert response.closed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('tenant', 'plugins_enabled'),
    [('tenant', False), ('', True), ('tenant', True)],
)
async def test_http_stream_closes_with_tenant_and_plugins(
    binding: str,
    version: str,
    tenant: str,
    plugins_enabled: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with _streaming_client(
        binding,
        version,
        monkeypatch,
        tenant=tenant,
        plugins_enabled=plugins_enabled,
    ) as (client, _, response):
        async with _bounded_events(_send(client)) as events:
            assert (
                await asyncio.wait_for(anext(events), timeout=2)
            ).task.id == 'task'
        assert response.closed


@pytest.mark.asyncio
async def test_http_legacy_subscribe_fallback_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with _streaming_client(
        'HTTP+JSON', '0.3', monkeypatch, subscribe_fallback=True
    ) as (client, _, response):
        events = cast(
            'AsyncGenerator[StreamResponse]',
            client.subscribe(SubscribeToTaskRequest(id='task')),
        )
        async with _bounded_events(events):
            assert (
                await asyncio.wait_for(anext(events), timeout=2)
            ).task.id == 'task'
        assert response.closed
