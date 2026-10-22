import asyncio
import socket

from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import AsyncExitStack

import grpc
import httpx
import pytest
import pytest_asyncio

from a2a.client.client import Client
from a2a.client.client_factory import ClientFactory
from a2a.helpers.proto_helpers import new_task_from_user_message
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.types.a2a_pb2 import (
    AgentCard,
    Message,
    Part,
    Role,
    SendMessageRequest,
    StreamResponse,
    TaskState,
)
from a2a.utils import TransportProtocol

from tests.integration import test_scenarios as scenarios


@pytest_asyncio.fixture
async def scenario_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[AsyncExitStack]:
    async with AsyncExitStack() as resources:
        monkeypatch.setattr(scenarios, '_test_resources', resources)
        yield resources


async def _assert_server_closed(address: str) -> None:
    url = httpx.URL(address if '://' in address else f'http://{address}')
    try:
        # Avoid combined IPv4/IPv6 refusal errors from localhost resolution.
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(url.host, url.port, family=socket.AF_INET),
            timeout=5,
        )
    except ConnectionRefusedError:
        return
    writer.close()
    await writer.wait_closed()
    pytest.fail(f'Test server still accepts connections at {address}')


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'transport',
    [TransportProtocol.GRPC, TransportProtocol.HTTP_JSON],
    ids=['grpc', 'rest'],
)
async def test_setup_failure_closes_resources(
    monkeypatch: pytest.MonkeyPatch,
    scenario_resources: AsyncExitStack,
    transport: TransportProtocol,
) -> None:
    address = ''
    http_client: httpx.AsyncClient | None = None

    def fail_create(factory: ClientFactory, card: AgentCard) -> Client:
        nonlocal address, http_client
        address = card.supported_interfaces[0].url
        http_client = factory._config.httpx_client
        raise RuntimeError('client setup failed')

    monkeypatch.setattr(ClientFactory, 'create', fail_create)
    with pytest.raises(RuntimeError, match='client setup failed'):
        await scenarios.create_client(
            scenarios.create_handler(_WaitingAgent(), use_legacy=False),
            scenarios.agent_card(),
            transport=transport,
        )

    if http_client is not None:
        assert http_client.is_closed
    await _assert_server_closed(address)


@pytest.mark.asyncio
async def test_started_grpc_server_is_closed_when_start_raises(
    monkeypatch: pytest.MonkeyPatch,
    scenario_resources: AsyncExitStack,
) -> None:
    server = grpc.aio.server()
    original_start = server.start
    original_bind = server.add_insecure_port
    address = ''

    def make_server() -> grpc.aio.Server:
        return server

    def bind_server(requested_address: str) -> int:
        nonlocal address
        port = original_bind(requested_address)
        address = f'localhost:{port}'
        return port

    async def fail_start() -> None:
        await original_start()
        raise RuntimeError('server startup failed')

    monkeypatch.setattr(scenarios.grpc.aio, 'server', make_server)
    monkeypatch.setattr(server, 'add_insecure_port', bind_server)
    monkeypatch.setattr(server, 'start', fail_start)
    try:
        with pytest.raises(RuntimeError, match='server startup failed'):
            await scenarios.create_client(
                scenarios.create_handler(_WaitingAgent(), use_legacy=False),
                scenarios.agent_card(),
            )
        await _assert_server_closed(address)
    finally:
        await server.stop(None)


class _WaitingAgent(AgentExecutor):
    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.stopped = asyncio.Event()

    async def execute(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        assert context.message is not None
        try:
            task = new_task_from_user_message(context.message)
            task.status.state = TaskState.TASK_STATE_WORKING
            await event_queue.enqueue_event(task)
            await self.release.wait()
        finally:
            self.stopped.set()

    async def cancel(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        self.release.set()


@pytest.mark.asyncio
@pytest.mark.timeout(10)
@pytest.mark.parametrize(
    'transport',
    [TransportProtocol.GRPC, TransportProtocol.HTTP_JSON],
    ids=['grpc', 'rest'],
)
@pytest.mark.parametrize('use_legacy', [False, True], ids=['v2', 'legacy'])
@pytest.mark.parametrize('ending', ['exception', 'cancellation'])
async def test_interrupted_scenario_closes_resources(
    scenario_resources: AsyncExitStack,
    transport: TransportProtocol,
    use_legacy: bool,
    ending: str,
) -> None:
    agent = _WaitingAgent()
    started = asyncio.Event()
    address = ''
    http_client: httpx.AsyncClient | None = None
    stream: AsyncGenerator[StreamResponse, None] | None = None

    async def run_scenario() -> None:
        nonlocal address, http_client, stream
        async with scenario_resources:
            card = scenarios.agent_card()
            client = await scenarios.create_client(
                scenarios.create_handler(agent, use_legacy),
                card,
                streaming=True,
                transport=transport,
            )
            address = card.supported_interfaces[0].url
            if transport == TransportProtocol.HTTP_JSON:
                http_client = client._transport.httpx_client
            stream = client.send_message(
                SendMessageRequest(
                    message=Message(
                        message_id='cleanup-test',
                        role=Role.ROLE_USER,
                        parts=[Part(text='start')],
                    )
                )
            )
            first = await anext(stream)
            assert scenarios.get_state(first) == TaskState.TASK_STATE_WORKING
            started.set()
            if ending == 'exception':
                raise RuntimeError('scenario failed')
            await asyncio.Event().wait()

    operation = asyncio.create_task(run_scenario())
    try:
        if ending == 'cancellation':
            await asyncio.wait_for(started.wait(), timeout=2)
            operation.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(operation, timeout=4)
        else:
            with pytest.raises(RuntimeError, match='scenario failed'):
                await asyncio.wait_for(operation, timeout=4)
        await asyncio.wait_for(agent.stopped.wait(), timeout=1)
        if http_client is not None:
            assert http_client.is_closed
        await _assert_server_closed(address)
    finally:
        agent.release.set()
        if not operation.done():
            operation.cancel()
        await asyncio.gather(operation, return_exceptions=True)
        if stream is not None:
            await stream.aclose()
