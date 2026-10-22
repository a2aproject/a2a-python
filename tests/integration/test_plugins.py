"""Plugins over real transports: JSON-RPC, HTTP+JSON, gRPC and v0.3 JSON-RPC."""

import asyncio
import contextvars

from collections.abc import AsyncGenerator, Callable
from typing import Any, NamedTuple

import grpc
import httpx
import pytest
import pytest_asyncio

from a2a import plugins
from a2a.client import ClientConfig, ClientFactory
from a2a.client.base_client import BaseClient
from a2a.helpers.proto_helpers import new_task_from_user_message
from a2a.plugins import (
    ClientRequest,
    ClientResponse,
    CompletePlugin,
    ExecutionPlugin,
    ServerRequest,
    ServerResponse,
    TaskEventInfo,
    TaskObserverPlugin,
)
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.context import ServerCallContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandlerV2, GrpcHandler
from a2a.server.routes import (
    create_agent_card_routes,
    create_jsonrpc_routes,
    create_rest_routes,
)
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import a2a_pb2_grpc
from a2a.types.a2a_pb2 import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    GetTaskRequest,
    Message,
    Part,
    Role,
    SendMessageRequest,
    TaskState,
)
from a2a.utils import TransportProtocol
from starlette.applications import Starlette


class Recorder(CompletePlugin, ExecutionPlugin, TaskObserverPlugin):
    """Records hooks and adds an ``x-trace`` header to outgoing calls."""

    name = 'recorder'

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    def hooks(self, name: str) -> list[Any]:
        return [arg for hook, arg in self.calls if hook == name]

    async def before_handler_run(self, request: ServerRequest) -> None:
        self.calls.append(('before_handler_run', request))

    async def after_handler_run(self, response: ServerResponse) -> None:
        self.calls.append(('after_handler_run', response))

    async def before_client_request(self, request: ClientRequest) -> None:
        params = dict(request.context.service_parameters or {})
        params['x-trace'] = 'trace-123'
        request.context.service_parameters = params
        self.calls.append(('before_client_request', request))

    async def after_client_request(self, response: ClientResponse) -> None:
        self.calls.append(('after_client_request', response))

    async def before_execution(self, request_context: RequestContext) -> None:
        self.calls.append(('before_execution', request_context))

    async def on_execution_event(self, request_context, event) -> None:
        self.calls.append(('on_execution_event', event))

    async def after_execution(self, request_context, error) -> None:
        self.calls.append(('after_execution', error))

    async def on_task_event(self, info: TaskEventInfo) -> None:
        self.calls.append(('on_task_event', info))


class TaskAgent(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue):
        task = context.current_task or new_task_from_user_message(
            context.message
        )
        if not context.current_task:
            await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue, task.id, task.context_id)
        await updater.start_work()
        await updater.add_artifact(parts=[Part(text='done')], name='out')
        await updater.complete()

    async def cancel(self, context: RequestContext, event_queue: EventQueue):
        pass


def agent_card(grpc_url: str = 'localhost:0') -> AgentCard:
    return AgentCard(
        name='Plugin Agent',
        description='Integration test agent',
        version='1.0',
        capabilities=AgentCapabilities(streaming=True),
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
            AgentInterface(
                protocol_binding=TransportProtocol.GRPC, url=grpc_url
            ),
        ],
    )


def send_request(text: str = 'hello') -> SendMessageRequest:
    return SendMessageRequest(
        message=Message(
            role=Role.ROLE_USER, message_id='m-1', parts=[Part(text=text)]
        )
    )


def incoming_headers(context: ServerCallContext) -> dict[str, str]:
    """Request headers: state['headers'] for HTTP, metadata for gRPC."""
    if 'grpc_context' in context.state:
        return dict(context.state['grpc_context'].invocation_metadata())
    return context.state['headers']


async def wait_for(predicate: Callable[[], bool], timeout: float = 5) -> None:
    async def poll() -> None:
        while not predicate():
            await asyncio.sleep(0.01)

    await asyncio.wait_for(poll(), timeout)


class Setup(NamedTuple):
    client: BaseClient
    server: Recorder
    caller: Recorder
    transport: str


def http_app(handler: DefaultRequestHandlerV2, card: AgentCard) -> Starlette:
    return Starlette(
        routes=[
            *create_agent_card_routes(agent_card=card, card_url='/card'),
            *create_jsonrpc_routes(
                request_handler=handler, rpc_url='/', enable_v0_3_compat=True
            ),
            *create_rest_routes(request_handler=handler),
        ]
    )


@pytest.fixture(autouse=True)
def restore_global():
    previous = plugins.get_global()
    yield
    plugins.set_global(previous)


@pytest_asyncio.fixture(
    params=[
        pytest.param(TransportProtocol.JSONRPC, id='JSON-RPC'),
        pytest.param(TransportProtocol.HTTP_JSON, id='HTTP+JSON'),
        pytest.param(TransportProtocol.GRPC, id='gRPC'),
    ]
)
async def setup(request) -> AsyncGenerator[Setup, None]:
    transport = request.param
    server_recorder, caller_recorder = Recorder(), Recorder()
    server = None
    card = agent_card()
    handler = DefaultRequestHandlerV2(
        agent_executor=TaskAgent(),
        task_store=InMemoryTaskStore(),
        agent_card=card,
        plugins=plugins.sequential(server_recorder),
    )
    # Clients pick up the global manager when created.
    plugins.set_global(plugins.sequential(caller_recorder))

    if transport == TransportProtocol.GRPC:
        server = grpc.aio.server()
        port = server.add_insecure_port('[::]:0')
        card = agent_card(f'localhost:{port}')
        a2a_pb2_grpc.add_A2AServiceServicer_to_server(
            GrpcHandler(handler), server
        )
        await server.start()
        config = ClientConfig(
            grpc_channel_factory=grpc.aio.insecure_channel,
            supported_protocol_bindings=[transport],
        )
    else:
        config = ClientConfig(
            httpx_client=httpx.AsyncClient(
                transport=httpx.ASGITransport(app=http_app(handler, card)),
                base_url='http://testserver',
            ),
            supported_protocol_bindings=[transport],
        )

    client = ClientFactory(config).create(card)
    yield Setup(client, server_recorder, caller_recorder, transport)

    await client.close()
    if server is not None:
        await server.stop(0)


@pytest.mark.asyncio
async def test_streaming_message_runs_hooks_on_both_sides(setup: Setup):
    events = [e async for e in setup.client.send_message(send_request())]
    await wait_for(lambda: bool(setup.server.hooks('after_execution')))

    assert events[-1].status_update.status.state == (
        TaskState.TASK_STATE_COMPLETED
    )

    # Client side.
    (call,) = setup.caller.hooks('before_client_request')
    assert call.method == 'SendStreamingMessage'
    client_responses = setup.caller.hooks('after_client_request')
    assert len(client_responses) == len(events) + 1
    assert client_responses[-1].is_final

    # Server side: the header added by the client plugin arrives.
    (run,) = setup.server.hooks('before_handler_run')
    assert run.method == 'SendStreamingMessage'
    assert incoming_headers(run.context)['x-trace'] == 'trace-123'
    assert run.agent_card.name == 'Plugin Agent'
    server_responses = setup.server.hooks('after_handler_run')
    assert server_responses[-1].is_final
    assert sum(r.is_stream_event for r in server_responses) == len(events)

    # Execution and task hooks.
    assert len(setup.server.hooks('on_execution_event')) == 4
    transitions = [
        (i.from_state, i.to_state) for i in setup.server.hooks('on_task_event')
    ]
    assert transitions[0] == (None, TaskState.TASK_STATE_SUBMITTED)
    assert transitions[-1][1] == TaskState.TASK_STATE_COMPLETED


@pytest.mark.asyncio
async def test_unary_call_runs_hooks_on_both_sides(setup: Setup):
    events = [e async for e in setup.client.send_message(send_request())]
    task_id = events[0].task.id
    setup.server.calls.clear()
    setup.caller.calls.clear()

    task = await setup.client.get_task(GetTaskRequest(id=task_id))

    assert task.id == task_id
    assert [r.method for r in setup.caller.hooks('before_client_request')] == [
        'GetTask'
    ]
    (response,) = setup.server.hooks('after_handler_run')
    assert response.method == 'GetTask'
    assert response.payload.id == task_id
    assert response.is_final


@pytest.mark.asyncio
async def test_v03_jsonrpc_requests_run_server_hooks():
    recorder = Recorder()
    handler = DefaultRequestHandlerV2(
        agent_executor=TaskAgent(),
        task_store=InMemoryTaskStore(),
        agent_card=agent_card(),
        plugins=plugins.sequential(recorder),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=http_app(handler, agent_card())),
        base_url='http://testserver',
    ) as http:
        response = await http.post(
            '/',
            json={
                'jsonrpc': '2.0',
                'id': '1',
                'method': 'message/send',
                'params': {
                    'message': {
                        'messageId': 'm-03',
                        'role': 'user',
                        'parts': [{'kind': 'text', 'text': 'hello'}],
                    }
                },
            },
        )

    assert response.status_code == 200
    assert response.json()['result']['status']['state'] == 'completed'
    (run,) = recorder.hooks('before_handler_run')
    assert run.method == 'SendMessage'
    assert recorder.hooks('after_handler_run')[-1].is_final


_tenant: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    'tenant', default=None
)


class TenantPlugin(CompletePlugin, ExecutionPlugin):
    """Forwards the incoming x-tenant header to outgoing calls."""

    name = 'tenant'

    async def before_execution(self, request_context: RequestContext) -> None:
        headers = request_context.call_context.state.get('headers', {})
        _tenant.set(headers.get('x-tenant'))

    async def before_client_request(self, request: ClientRequest) -> None:
        if tenant := _tenant.get():
            params = dict(request.context.service_parameters or {})
            params['x-tenant'] = tenant
            request.context.service_parameters = params


@pytest.mark.asyncio
async def test_two_agent_chain_propagates_state_through_execution():
    downstream = Recorder()
    downstream_handler = DefaultRequestHandlerV2(
        agent_executor=TaskAgent(),
        task_store=InMemoryTaskStore(),
        agent_card=agent_card(),
        plugins=plugins.sequential(downstream),
    )
    downstream_http = httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=http_app(downstream_handler, agent_card())
        ),
        base_url='http://testserver',
    )
    tenant_plugin = TenantPlugin()
    plugins.set_global(plugins.sequential(tenant_plugin))

    class Orchestrator(AgentExecutor):
        async def execute(self, context, event_queue):
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
            client = ClientFactory(
                ClientConfig(httpx_client=downstream_http)
            ).create(agent_card())
            async for _ in client.send_message(send_request('forwarded')):
                pass
            await TaskUpdater(event_queue, task.id, task.context_id).complete()

        async def cancel(self, context, event_queue):
            pass

    upstream_handler = DefaultRequestHandlerV2(
        agent_executor=Orchestrator(),
        task_store=InMemoryTaskStore(),
        agent_card=agent_card(),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=http_app(upstream_handler, agent_card())
        ),
        base_url='http://testserver',
    ) as http:
        response = await http.post(
            '/',
            headers={'x-tenant': 'acme', 'A2A-Version': '1.0'},
            json={
                'jsonrpc': '2.0',
                'id': '1',
                'method': 'SendMessage',
                'params': {
                    'message': {
                        'messageId': 'm-up',
                        'role': 'ROLE_USER',
                        'parts': [{'text': 'plan'}],
                    }
                },
            },
        )
    await downstream_http.aclose()

    assert response.status_code == 200, response.text
    (run,) = downstream.hooks('before_handler_run')
    assert run.context.state['headers']['x-tenant'] == 'acme'
