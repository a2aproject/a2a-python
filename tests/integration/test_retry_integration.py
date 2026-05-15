from unittest.mock import ANY, AsyncMock

import httpx
import pytest

from a2a.client.base_client import BaseClient
from a2a.client.client import ClientConfig
from a2a.client.client_factory import ClientFactory
from a2a.client.transports.retry import RetryTransport
from a2a.server.request_handlers import RequestHandler
from a2a.server.routes import create_jsonrpc_routes, create_rest_routes
from a2a.types.a2a_pb2 import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    GetTaskRequest,
    Message,
    Part,
    Role,
    SendMessageRequest,
    Task,
    TaskState,
    TaskStatus,
)
from a2a.utils.constants import TransportProtocol
from starlette.applications import Starlette


TASK_RESPONSE = Task(
    id='task-retry-integration',
    context_id='ctx-retry-integration',
    status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
)


def _wrap_with_transient_503(app, fail_count: int = 2):
    state = {'count': 0}

    async def middleware(scope, receive, send):
        if scope['type'] == 'http' and state['count'] < fail_count:
            state['count'] += 1
            await send(
                {
                    'type': 'http.response.start',
                    'status': 503,
                    'headers': [[b'content-type', b'text/plain']],
                }
            )
            await send(
                {
                    'type': 'http.response.body',
                    'body': b'Service Unavailable',
                }
            )
            return
        await app(scope, receive, send)

    return middleware, state


@pytest.fixture
def mock_request_handler() -> AsyncMock:
    handler = AsyncMock(spec=RequestHandler)
    handler.on_get_task.return_value = TASK_RESPONSE
    handler.on_message_send.return_value = TASK_RESPONSE
    return handler


@pytest.fixture
def agent_card() -> AgentCard:
    return AgentCard(
        name='Retry Integration Agent',
        description='Agent for retry integration testing.',
        version='1.0.0',
        capabilities=AgentCapabilities(streaming=False),
        skills=[],
        default_input_modes=['text/plain'],
        default_output_modes=['text/plain'],
        supported_interfaces=[
            AgentInterface(
                protocol_binding=TransportProtocol.HTTP_JSON,
                url='http://testserver',
            ),
            AgentInterface(
                protocol_binding=TransportProtocol.JSONRPC,
                url='http://testserver',
            ),
        ],
    )


@pytest.mark.asyncio
async def test_retry_with_client_factory_rest(
    mock_request_handler: AsyncMock,
    agent_card: AgentCard,
) -> None:
    rest_routes = create_rest_routes(mock_request_handler)
    app = Starlette(routes=[*rest_routes])
    failing_app, state = _wrap_with_transient_503(app, fail_count=2)

    httpx_client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=failing_app),
    )

    factory = ClientFactory(
        config=ClientConfig(
            httpx_client=httpx_client,
            supported_protocol_bindings=[TransportProtocol.HTTP_JSON],
        )
    )
    client = factory.create(agent_card)

    assert isinstance(client, BaseClient)
    original_transport = client._transport
    client._transport = RetryTransport(
        original_transport,
        max_retries=3,
        base_delay=0.01,
        max_delay=0.1,
        jitter=False,
    )

    params = GetTaskRequest(id=TASK_RESPONSE.id)
    result = await client.get_task(request=params)

    assert result.id == TASK_RESPONSE.id
    assert state['count'] == 2
    mock_request_handler.on_get_task.assert_awaited_once_with(params, ANY)

    await client.close()


@pytest.mark.asyncio
async def test_retry_with_client_factory_jsonrpc(
    mock_request_handler: AsyncMock,
    agent_card: AgentCard,
) -> None:
    jsonrpc_routes = create_jsonrpc_routes(
        request_handler=mock_request_handler,
        rpc_url='/',
    )
    app = Starlette(routes=[*jsonrpc_routes])
    failing_app, state = _wrap_with_transient_503(app, fail_count=2)

    httpx_client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=failing_app),
    )

    factory = ClientFactory(
        config=ClientConfig(
            httpx_client=httpx_client,
            supported_protocol_bindings=[TransportProtocol.JSONRPC],
        )
    )
    client = factory.create(agent_card)

    assert isinstance(client, BaseClient)
    original_transport = client._transport
    client._transport = RetryTransport(
        original_transport,
        max_retries=3,
        base_delay=0.01,
        max_delay=0.1,
        jitter=False,
    )

    params = GetTaskRequest(id=TASK_RESPONSE.id)
    result = await client.get_task(request=params)

    assert result.id == TASK_RESPONSE.id
    assert state['count'] == 2
    mock_request_handler.on_get_task.assert_awaited_once_with(params, ANY)

    await client.close()


@pytest.mark.asyncio
async def test_retry_send_message_blocking(
    mock_request_handler: AsyncMock,
    agent_card: AgentCard,
) -> None:
    rest_routes = create_rest_routes(mock_request_handler)
    app = Starlette(routes=[*rest_routes])
    failing_app, state = _wrap_with_transient_503(app, fail_count=1)

    httpx_client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=failing_app),
    )

    factory = ClientFactory(
        config=ClientConfig(
            httpx_client=httpx_client,
            supported_protocol_bindings=[TransportProtocol.HTTP_JSON],
        )
    )
    client = factory.create(agent_card)

    assert isinstance(client, BaseClient)
    # Disable streaming to force a single non-streaming call.
    client._config.streaming = False
    original_transport = client._transport
    client._transport = RetryTransport(
        original_transport,
        max_retries=2,
        base_delay=0.01,
        jitter=False,
    )

    message_to_send = Message(
        role=Role.ROLE_USER,
        message_id='msg-retry-test',
        parts=[Part(text='Hello retry')],
    )
    params = SendMessageRequest(message=message_to_send)

    events = [event async for event in client.send_message(request=params)]

    assert len(events) == 1
    stream_response = events[0]
    assert stream_response.HasField('task')
    assert stream_response.task.id == TASK_RESPONSE.id
    assert state['count'] == 1
    mock_request_handler.on_message_send.assert_awaited_once_with(params, ANY)

    await client.close()
