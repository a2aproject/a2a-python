from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from a2a import plugins
from a2a.client.base_client import BaseClient
from a2a.client.client import ClientCallContext, ClientConfig
from a2a.client.client_factory import ClientFactory
from a2a.client.interceptors import AfterArgs, BeforeArgs, ClientCallInterceptor
from a2a.client.transports.base import ClientTransport
from a2a.plugins import ClientRequest, ClientResponse
from a2a.types.a2a_pb2 import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    CancelTaskRequest,
    DeleteTaskPushNotificationConfigRequest,
    GetExtendedAgentCardRequest,
    GetTaskPushNotificationConfigRequest,
    GetTaskRequest,
    ListTaskPushNotificationConfigsRequest,
    ListTasksRequest,
    Message,
    Part,
    Role,
    SendMessageRequest,
    SendMessageResponse,
    StreamResponse,
    SubscribeToTaskRequest,
    Task,
    TaskPushNotificationConfig,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)


class Recorder(plugins.ClientPlugin):
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    def hooks(self, name: str) -> list[Any]:
        return [arg for hook, arg in self.calls if hook == name]

    async def before_client_request(self, request: ClientRequest) -> None:
        params = dict(request.context.service_parameters or {})
        params['x-trace'] = 'abc'
        request.context.service_parameters = params
        self.calls.append(('before_client_request', request))

    async def after_client_request(self, response: ClientResponse) -> None:
        self.calls.append(('after_client_request', response))


def card(streaming: bool = True) -> AgentCard:
    return AgentCard(
        name='Remote',
        version='1.0',
        capabilities=AgentCapabilities(streaming=streaming),
        supported_interfaces=[
            AgentInterface(url='http://remote:9000', protocol_binding='JSONRPC')
        ],
    )


def client(
    recorder: Recorder | None,
    transport: AsyncMock,
    interceptors: list[ClientCallInterceptor] | None = None,
    streaming: bool = True,
) -> BaseClient:
    return BaseClient(
        card(streaming),
        ClientConfig(streaming=streaming),
        transport,
        interceptors or [],
        plugins=plugins.sequential(recorder) if recorder else None,
    )


def send_request() -> SendMessageRequest:
    return SendMessageRequest(
        message=Message(
            role=Role.ROLE_USER, message_id='m-1', parts=[Part(text='hi')]
        )
    )


def status_event(state: TaskState) -> StreamResponse:
    return StreamResponse(
        status_update=TaskStatusUpdateEvent(
            task_id='t-1', status=TaskStatus(state=state)
        )
    )


@pytest.fixture(autouse=True)
def restore_global():
    previous = plugins.get_global()
    yield
    plugins.set_global(previous)


@pytest.mark.asyncio
async def test_unary_call_runs_hooks():
    recorder = Recorder()
    transport = AsyncMock(spec=ClientTransport)
    transport.get_task.return_value = Task(id='t-1')

    result = await client(recorder, transport).get_task(
        GetTaskRequest(id='t-1')
    )

    assert result.id == 't-1'
    (request,) = recorder.hooks('before_client_request')
    assert request.method == 'get_task'
    assert request.agent_card.name == 'Remote'
    (response,) = recorder.hooks('after_client_request')
    assert response.payload is result
    assert response.is_final
    assert response.context is request.context
    # The header added by the plugin reaches the transport.
    sent_context = transport.get_task.call_args.kwargs['context']
    assert sent_context.service_parameters['x-trace'] == 'abc'


@pytest.mark.asyncio
async def test_non_streaming_send_message_is_a_unary_call():
    recorder = Recorder()
    transport = AsyncMock(spec=ClientTransport)
    transport.send_message.return_value = SendMessageResponse(
        task=Task(id='t-1')
    )

    events = [
        e
        async for e in client(
            recorder, transport, streaming=False
        ).send_message(send_request())
    ]

    assert len(events) == 1
    assert recorder.hooks('before_client_request')[0].method == 'send_message'
    (response,) = recorder.hooks('after_client_request')
    assert response.is_final


@pytest.mark.asyncio
async def test_streaming_reports_events_and_final_call():
    recorder = Recorder()
    transport = AsyncMock(spec=ClientTransport)
    streamed = [
        status_event(TaskState.TASK_STATE_WORKING),
        status_event(TaskState.TASK_STATE_COMPLETED),
    ]

    async def stream(request, *, context=None):
        for e in streamed:
            yield e

    transport.send_message_streaming = stream

    events = [
        e
        async for e in client(recorder, transport).send_message(send_request())
    ]

    assert events == streamed
    (request,) = recorder.hooks('before_client_request')
    assert request.method == 'send_message_streaming'
    responses = recorder.hooks('after_client_request')
    assert [r.payload for r in responses[:-1]] == streamed
    assert all(r.is_stream_event and not r.is_final for r in responses[:-1])
    assert responses[-1].is_final
    assert responses[-1].payload is None
    assert responses[-1].error is None


@pytest.mark.asyncio
async def test_stream_stopped_early_gets_final_call():
    recorder = Recorder()
    transport = AsyncMock(spec=ClientTransport)

    async def stream(request, *, context=None):
        for _ in range(10):
            yield status_event(TaskState.TASK_STATE_WORKING)

    transport.send_message_streaming = stream
    events = client(recorder, transport).send_message(send_request())
    await anext(events)
    await events.aclose()

    assert recorder.hooks('after_client_request')[-1].is_final


@pytest.mark.asyncio
async def test_transport_errors_reach_after_client_request():
    recorder = Recorder()
    transport = AsyncMock(spec=ClientTransport)
    transport.get_task.side_effect = ConnectionError('down')

    with pytest.raises(ConnectionError):
        await client(recorder, transport).get_task(GetTaskRequest(id='t-1'))

    (response,) = recorder.hooks('after_client_request')
    assert isinstance(response.error, ConnectionError)

    async def failing(request, *, context=None):
        yield status_event(TaskState.TASK_STATE_WORKING)
        raise ConnectionError('dropped')

    transport.send_message_streaming = failing
    recorder.calls.clear()
    with pytest.raises(ConnectionError):
        async for _ in client(recorder, transport).send_message(send_request()):
            pass
    assert isinstance(
        recorder.hooks('after_client_request')[-1].error, ConnectionError
    )


@pytest.mark.asyncio
async def test_plugins_wrap_interceptors_including_early_return():
    order: list[str] = []

    class Order(plugins.ClientPlugin):
        async def before_client_request(self, request):
            order.append('plugin.before')

        async def after_client_request(self, response):
            order.append('plugin.after')

    class Cached(ClientCallInterceptor):
        async def before(self, args: BeforeArgs) -> None:
            order.append('interceptor.before')
            args.early_return = Task(id='cached')

        async def after(self, args: AfterArgs) -> None:
            order.append('interceptor.after')

    transport = AsyncMock(spec=ClientTransport)
    c = BaseClient(
        card(),
        ClientConfig(),
        transport,
        [Cached()],
        plugins=plugins.sequential(Order()),
    )

    result = await c.get_task(GetTaskRequest(id='t-1'))

    assert result.id == 'cached'
    transport.get_task.assert_not_called()
    assert order == [
        'plugin.before',
        'interceptor.before',
        'interceptor.after',
        'plugin.after',
    ]


@pytest.mark.asyncio
async def test_before_client_request_can_cancel_the_call():
    class Deny(plugins.ClientPlugin):
        async def before_client_request(self, request):
            raise PermissionError('no')

    transport = AsyncMock(spec=ClientTransport)
    c = BaseClient(
        card(),
        ClientConfig(),
        transport,
        [],
        plugins=plugins.sequential(Deny()),
    )

    with pytest.raises(PermissionError):
        await c.get_task(GetTaskRequest(id='t-1'))
    with pytest.raises(PermissionError):
        async for _ in c.send_message(send_request()):
            pass
    transport.get_task.assert_not_called()


@pytest.mark.asyncio
async def test_existing_context_is_passed_through():
    recorder = Recorder()
    transport = AsyncMock(spec=ClientTransport)
    transport.get_task.return_value = Task(id='t-1')
    context = ClientCallContext(state={'k': 'v'})

    await client(recorder, transport).get_task(
        GetTaskRequest(id='t-1'), context=context
    )

    assert recorder.hooks('before_client_request')[0].context is context


@pytest.mark.asyncio
async def test_no_plugins_keeps_context_none():
    transport = AsyncMock(spec=ClientTransport)
    transport.get_task.return_value = Task(id='t-1')

    await client(None, transport).get_task(GetTaskRequest(id='t-1'))

    assert transport.get_task.call_args.kwargs['context'] is None


def test_factory_clients_use_the_global_manager():
    manager = plugins.sequential(Recorder())
    plugins.set_global(manager)

    c = ClientFactory(ClientConfig(httpx_client=httpx.AsyncClient())).create(
        card()
    )

    assert isinstance(c, BaseClient)
    assert c._plugins is manager


@pytest.mark.parametrize(
    ('call', 'method'),
    [
        (lambda c: c.list_tasks(ListTasksRequest()), 'list_tasks'),
        (lambda c: c.cancel_task(CancelTaskRequest(id='t-1')), 'cancel_task'),
        (
            lambda c: c.create_task_push_notification_config(
                TaskPushNotificationConfig(task_id='t-1', url='http://hook')
            ),
            'create_task_push_notification_config',
        ),
        (
            lambda c: c.get_task_push_notification_config(
                GetTaskPushNotificationConfigRequest(task_id='t-1', id='c-1')
            ),
            'get_task_push_notification_config',
        ),
        (
            lambda c: c.list_task_push_notification_configs(
                ListTaskPushNotificationConfigsRequest(task_id='t-1')
            ),
            'list_task_push_notification_configs',
        ),
        (
            lambda c: c.delete_task_push_notification_config(
                DeleteTaskPushNotificationConfigRequest(task_id='t-1', id='c-1')
            ),
            'delete_task_push_notification_config',
        ),
        (
            lambda c: c.get_extended_agent_card(GetExtendedAgentCardRequest()),
            'get_extended_agent_card',
        ),
    ],
)
@pytest.mark.asyncio
async def test_unary_methods_report_method_names(call, method):
    recorder = Recorder()

    await call(client(recorder, AsyncMock(spec=ClientTransport)))

    assert [r.method for r in recorder.hooks('before_client_request')] == [
        method
    ]
    assert recorder.hooks('after_client_request')[-1].method == method


@pytest.mark.asyncio
async def test_subscribe_reports_method_name():
    recorder = Recorder()
    transport = AsyncMock(spec=ClientTransport)

    async def stream(request, *, context=None):
        yield status_event(TaskState.TASK_STATE_WORKING)

    transport.subscribe = stream

    async for _ in client(recorder, transport).subscribe(
        SubscribeToTaskRequest(id='t-1')
    ):
        pass

    assert recorder.hooks('before_client_request')[0].method == 'subscribe'
    assert all(
        r.method == 'subscribe' for r in recorder.hooks('after_client_request')
    )
