"""The OpenTelemetry plugin's construction and switches."""

import asyncio
import builtins
import importlib
import inspect
import re

from unittest.mock import AsyncMock, MagicMock

import pytest

from a2a import plugins
from a2a.client.base_client import BaseClient
from a2a.client.client import ClientCallContext, ClientConfig
from a2a.client.errors import A2AClientError
from a2a.client.transports.base import ClientTransport
from a2a.helpers.proto_helpers import new_task_from_user_message
from a2a.plugins import ClientRequest, ClientResponse
from a2a.plugins.otel import (
    OpenTelemetryPlugin,
    _semconv,
    plugin as otel_plugin,
)
from a2a.server.agent_execution import AgentExecutor
from a2a.server.context import ServerCallContext
from a2a.server.request_handlers import DefaultRequestHandlerV2
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import TaskNotFoundError, a2a_pb2
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
    ListTasksResponse,
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
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from opentelemetry.trace import SpanKind, StatusCode


def test_plugin_is_a_client_and_server_plugin():
    assert isinstance(OpenTelemetryPlugin(), plugins.CompletePlugin)


def test_plugin_registers_with_the_manager():
    manager = plugins.sequential(OpenTelemetryPlugin())

    assert manager.has_client_hooks
    assert manager.has_server_hooks


def test_tracer_is_taken_from_the_given_provider():
    provider = MagicMock(spec=TracerProvider)

    plugin = OpenTelemetryPlugin(provider)

    assert plugin._tracer is provider.get_tracer.return_value
    name, version = provider.get_tracer.call_args.args
    assert name == 'a2a-python-sdk.otel'
    assert version == otel_plugin._SDK_VERSION


def test_no_provider_falls_back_to_the_global_one():
    """The application owns the provider, so None means whatever it set."""
    plugin = OpenTelemetryPlugin()

    assert plugin._enabled
    assert plugin._tracer is not None


@pytest.mark.parametrize('value', ['false', 'False', 'FALSE', 'no'])
def test_env_var_disables_the_plugin(monkeypatch, value):
    monkeypatch.setenv(otel_plugin.ENABLED_ENV_VAR, value)

    plugin = OpenTelemetryPlugin()

    assert not plugin._enabled
    assert not hasattr(plugin, '_tracer')


@pytest.mark.asyncio
async def test_disabled_plugin_creates_no_span_or_header(monkeypatch):
    """A disabled plugin injects no traceparent and leaves the call intact."""
    monkeypatch.setenv(otel_plugin.ENABLED_ENV_VAR, 'false')
    sent = {}

    async def get_task(request, *, context=None):
        sent.update(context.service_parameters or {})
        return Task(id='t-1')

    transport = AsyncMock(spec=ClientTransport)
    transport.get_task = get_task
    client = BaseClient(
        AgentCard(name='Remote'),
        ClientConfig(),
        transport,
        [],
        plugins=plugins.sequential(OpenTelemetryPlugin()),
    )

    result = await client.get_task(GetTaskRequest(id='t-1'))

    assert result.id == 't-1'
    assert 'traceparent' not in sent


def test_plugin_is_enabled_by_default(monkeypatch):
    monkeypatch.delenv(otel_plugin.ENABLED_ENV_VAR, raising=False)

    assert OpenTelemetryPlugin()._enabled


def test_missing_opentelemetry_raises_with_an_install_hint(monkeypatch):
    """Asking for this plugin is intent, so a silent no-op would hide it."""
    monkeypatch.setattr(otel_plugin, '_otel_installed', False)

    with pytest.raises(ImportError, match=r'a2a-sdk\[telemetry\]'):
        OpenTelemetryPlugin()


def test_module_imports_without_opentelemetry(monkeypatch):
    """The module must import cleanly when the extra is absent."""
    real_import = builtins.__import__

    def without_otel(name, *args, **kwargs):
        if name.startswith('opentelemetry'):
            raise ImportError('simulated missing extra')
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', without_otel)
    reloaded = importlib.reload(otel_plugin)
    monkeypatch.undo()

    try:
        assert reloaded._otel_installed is False
    finally:
        importlib.reload(otel_plugin)


CARD = AgentCard(
    name='Remote',
    description='Answers questions.',
    version='1.0',
    capabilities=AgentCapabilities(streaming=True),
)
INTERFACE = AgentInterface(
    url='http://remote:9000',
    protocol_binding='JSONRPC',
    protocol_version='1.0',
    tenant='billing',
)


@pytest.fixture
def exporter():
    return InMemorySpanExporter()


@pytest.fixture
def make_client(exporter):
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    def make(transport, *, card=CARD, interface=INTERFACE, streaming=True):
        return BaseClient(
            card,
            ClientConfig(streaming=streaming),
            transport,
            [],
            interface=interface,
            plugins=plugins.sequential(
                otel_plugin.OpenTelemetryPlugin(provider)
            ),
        )

    return make


@pytest.fixture
def make_handler(exporter):
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    async def make(*tasks, executor=None, card=CARD):
        store = InMemoryTaskStore()
        for stored in tasks:
            await store.save(stored, ServerCallContext())
        return DefaultRequestHandlerV2(
            agent_executor=executor or TaskAgent(),
            task_store=store,
            agent_card=card,
            plugins=plugins.sequential(
                otel_plugin.OpenTelemetryPlugin(provider)
            ),
        )

    return make


def server_context(**state):
    return ServerCallContext(state={'headers': {}, **state})


class TaskAgent(AgentExecutor):
    """SUBMITTED -> WORKING -> COMPLETED, all on one task."""

    async def execute(self, context, event_queue):
        task = context.current_task or new_task_from_user_message(
            context.message
        )
        if not context.current_task:
            await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue, task.id, task.context_id)
        await updater.start_work()
        await updater.complete()

    async def cancel(self, context, event_queue):
        pass


class SlowAgent(AgentExecutor):
    """Never finishes, so a call to it can only end by cancellation."""

    async def execute(self, context, event_queue):
        await asyncio.sleep(10)

    async def cancel(self, context, event_queue):
        pass


def only_span(exporter):
    (span,) = exporter.get_finished_spans()
    return span


def task(task_id='t-1', context_id='c-1', state=TaskState.TASK_STATE_COMPLETED):
    return Task(
        id=task_id, context_id=context_id, status=TaskStatus(state=state)
    )


def status_event(state):
    return StreamResponse(
        status_update=TaskStatusUpdateEvent(
            task_id='t-1', context_id='c-1', status=TaskStatus(state=state)
        )
    )


def send_request(**message_fields):
    return SendMessageRequest(
        message=Message(
            role=Role.ROLE_USER,
            message_id='m-1',
            parts=[Part(text='hi')],
            **message_fields,
        )
    )


def streaming(*events, error=None):
    async def stream(request, *, context=None):
        for event in events:
            yield event
        if error is not None:
            raise error

    return stream


def assert_absent(span, *keys):
    assert not set(keys) & set(span.attributes)


CALLS = [
    ('get_task', GetTaskRequest(id='t-1'), 'GetTask'),
    ('cancel_task', CancelTaskRequest(id='t-1'), 'CancelTask'),
    ('list_tasks', ListTasksRequest(), 'ListTasks'),
    (
        'create_task_push_notification_config',
        TaskPushNotificationConfig(task_id='t-1'),
        'CreateTaskPushNotificationConfig',
    ),
    (
        'get_task_push_notification_config',
        GetTaskPushNotificationConfigRequest(task_id='t-1', id='cfg'),
        'GetTaskPushNotificationConfig',
    ),
    (
        'list_task_push_notification_configs',
        ListTaskPushNotificationConfigsRequest(task_id='t-1'),
        'ListTaskPushNotificationConfigs',
    ),
    (
        'delete_task_push_notification_config',
        DeleteTaskPushNotificationConfigRequest(task_id='t-1', id='cfg'),
        'DeleteTaskPushNotificationConfig',
    ),
    (
        'get_extended_agent_card',
        GetExtendedAgentCardRequest(),
        'GetExtendedAgentCard',
    ),
]


@pytest.mark.parametrize(('method', 'request_', 'name'), CALLS)
@pytest.mark.asyncio
async def test_span_name_and_kind(
    exporter, make_client, method, request_, name
):
    """Unary calls produce a CLIENT span named with the semconv method name."""
    transport = AsyncMock(spec=ClientTransport)
    getattr(transport, method).return_value = None

    await getattr(make_client(transport), method)(request_)

    span = only_span(exporter)
    assert span.name == name
    assert span.attributes[_semconv.A2A_METHOD_NAME] == name
    assert span.kind is SpanKind.CLIENT


@pytest.mark.asyncio
async def test_streaming_methods_use_their_own_names(exporter, make_client):
    """Streaming calls are named SendStreamingMessage and SubscribeToTask."""
    transport = AsyncMock(spec=ClientTransport)
    transport.send_message_streaming = streaming()
    transport.subscribe = streaming()
    client = make_client(transport)

    async for _ in client.send_message(send_request()):
        pass
    async for _ in client.subscribe(SubscribeToTaskRequest(id='t-1')):
        pass

    names = [s.name for s in exporter.get_finished_spans()]
    assert names == ['SendStreamingMessage', 'SubscribeToTask']


@pytest.mark.asyncio
async def test_unknown_method_name_is_used_as_is(exporter):
    """A method with no semconv name keeps its SDK name."""
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    plugin = otel_plugin.OpenTelemetryPlugin(provider)
    request = ClientRequest(
        method='custom_call',
        payload=None,
        agent_card=CARD,
        context=ClientCallContext(),
    )

    await plugin.before_client_request(request)
    await plugin.after_client_request(
        ClientResponse('custom_call', None, request.context)
    )

    assert only_span(exporter).name == 'custom_call'


def test_method_names_cover_the_a2a_service():
    """Every A2AService RPC and every SDK client method has a span name."""
    service = a2a_pb2.DESCRIPTOR.services_by_name['A2AService']
    sdk_methods = set(
        re.findall(r"method='([a-z_]+)'", inspect.getsource(BaseClient))
    )

    assert {_semconv.get_method_name(m) for m in sdk_methods} == {
        method.name for method in service.methods
    }


@pytest.mark.parametrize('side', ['client', 'server'])
@pytest.mark.parametrize(
    ('state', 'expected'),
    [
        (TaskState.TASK_STATE_COMPLETED, 'TASK_STATE_COMPLETED'),
        (TaskState.TASK_STATE_WORKING, 'TASK_STATE_WORKING'),
        (TaskState.TASK_STATE_UNSPECIFIED, None),
    ],
)
@pytest.mark.asyncio
async def test_unary_call_reports_task_attributes(
    exporter, make_client, make_handler, side, state, expected
):
    """A unary call reports the task ID, context ID and a set task state."""
    if side == 'client':
        transport = AsyncMock(spec=ClientTransport)
        transport.get_task.return_value = task(state=state)
        await make_client(transport).get_task(GetTaskRequest(id='t-1'))
    else:
        handler = await make_handler(task(state=state))
        await handler.on_get_task(GetTaskRequest(id='t-1'), server_context())

    attributes = only_span(exporter).attributes
    assert attributes[_semconv.A2A_TASK_ID] == 't-1'
    assert attributes[_semconv.GEN_AI_CONVERSATION_ID] == 'c-1'
    assert attributes.get(_semconv.A2A_TASK_STATE) == expected


@pytest.mark.parametrize(
    'events',
    [
        [
            status_event(TaskState.TASK_STATE_WORKING),
            status_event(TaskState.TASK_STATE_COMPLETED),
        ],
        [status_event(TaskState.TASK_STATE_COMPLETED)],
        [],
    ],
    ids=['state-changes', 'single-state', 'no-events'],
)
@pytest.mark.asyncio
async def test_stream_reports_ids_but_not_state(exporter, make_client, events):
    """A stream reports task and context IDs but never the task state."""
    transport = AsyncMock(spec=ClientTransport)
    transport.send_message_streaming = streaming(*events)

    async for _ in make_client(transport).send_message(send_request()):
        pass

    span = only_span(exporter)
    assert_absent(span, _semconv.A2A_TASK_STATE)
    if events:
        assert span.attributes[_semconv.A2A_TASK_ID] == 't-1'
        assert span.attributes[_semconv.GEN_AI_CONVERSATION_ID] == 'c-1'
    else:
        assert_absent(span, _semconv.A2A_TASK_ID)


@pytest.mark.asyncio
async def test_server_stream_reports_ids_but_not_state(exporter, make_handler):
    """A server stream moving through states reports IDs, never the state."""
    handler = await make_handler()

    events = [
        event
        async for event in handler.on_message_send_stream(
            send_request(), server_context()
        )
    ]

    span = only_span(exporter)
    assert len(events) > 1
    assert span.kind is SpanKind.SERVER
    assert_absent(span, _semconv.A2A_TASK_STATE)
    assert span.attributes[_semconv.A2A_TASK_ID] == events[0].id
    assert span.attributes[_semconv.GEN_AI_CONVERSATION_ID]


@pytest.mark.parametrize('side', ['client', 'server'])
@pytest.mark.parametrize('count', [0, 1, 3])
@pytest.mark.asyncio
async def test_list_tasks_never_reports_task_ids(
    exporter, make_client, make_handler, side, count
):
    """ListTasks reports no task attributes, whatever the number of tasks."""
    tasks = [task(f't-{i}', f'c-{i}') for i in range(count)]
    request = ListTasksRequest(context_id='filter')
    if side == 'client':
        transport = AsyncMock(spec=ClientTransport)
        transport.list_tasks.return_value = ListTasksResponse(tasks=tasks)
        await make_client(transport).list_tasks(request)
    else:
        handler = await make_handler(*tasks)
        await handler.on_list_tasks(request, server_context())

    assert_absent(
        only_span(exporter),
        _semconv.A2A_TASK_ID,
        _semconv.A2A_TASK_STATE,
        _semconv.GEN_AI_CONVERSATION_ID,
    )


@pytest.mark.parametrize(
    ('method', 'request_', 'response'),
    [
        ('get_task', GetTaskRequest(id='t-1'), task()),
        (
            'get_task_push_notification_config',
            GetTaskPushNotificationConfigRequest(task_id='t-1', id='cfg-9'),
            TaskPushNotificationConfig(task_id='t-1', id='cfg-9'),
        ),
    ],
    ids=['request-id-is-task-id', 'push-config-id-is-not'],
)
@pytest.mark.asyncio
async def test_task_id_comes_from_the_right_field(
    exporter, make_client, method, request_, response
):
    """The task ID is read from the right field, never a config ID."""
    transport = AsyncMock(spec=ClientTransport)
    getattr(transport, method).return_value = response

    await getattr(make_client(transport), method)(request_)

    assert only_span(exporter).attributes[_semconv.A2A_TASK_ID] == 't-1'


ADDRESS_KEYS = (
    _semconv.SERVER_ADDRESS,
    _semconv.SERVER_PORT,
    _semconv.A2A_PROTOCOL_VERSION,
    _semconv.A2A_TENANT,
)


@pytest.mark.parametrize(
    ('interface', 'request_tenant', 'expected'),
    [
        (
            INTERFACE,
            '',
            {
                _semconv.SERVER_ADDRESS: 'remote',
                _semconv.SERVER_PORT: 9000,
                _semconv.A2A_PROTOCOL_VERSION: '1.0',
                _semconv.A2A_TENANT: 'billing',
            },
        ),
        (
            AgentInterface(url='https://remote.example.com'),
            '',
            {_semconv.SERVER_ADDRESS: 'remote.example.com'},
        ),
        (
            AgentInterface(url='localhost:50051', protocol_binding='GRPC'),
            '',
            {_semconv.SERVER_ADDRESS: 'localhost', _semconv.SERVER_PORT: 50051},
        ),
        (None, '', {}),
        (
            INTERFACE,
            'sales',
            {
                _semconv.SERVER_ADDRESS: 'remote',
                _semconv.SERVER_PORT: 9000,
                _semconv.A2A_PROTOCOL_VERSION: '1.0',
                _semconv.A2A_TENANT: 'sales',
            },
        ),
    ],
    ids=['full', 'no-port', 'grpc', 'no-interface', 'request-tenant-wins'],
)
@pytest.mark.asyncio
async def test_interface_and_tenant_attributes(
    exporter, make_client, interface, request_tenant, expected
):
    """Address, port, protocol and tenant follow the selected interface."""
    transport = AsyncMock(spec=ClientTransport)
    transport.get_task.return_value = task()

    await make_client(transport, interface=interface).get_task(
        GetTaskRequest(id='t-1', tenant=request_tenant)
    )

    attributes = only_span(exporter).attributes
    actual = {k: attributes[k] for k in ADDRESS_KEYS if k in attributes}
    assert actual == expected


@pytest.mark.parametrize(
    ('tenant', 'expected'), [('billing', 'billing'), ('', None)]
)
@pytest.mark.asyncio
async def test_server_tenant_comes_from_the_call_context(
    exporter, make_handler, tenant, expected
):
    """The server reports the call context's tenant, and no address."""
    handler = await make_handler(task())
    context = server_context()
    context.tenant = tenant

    await handler.on_get_task(GetTaskRequest(id='t-1'), context)

    attributes = only_span(exporter).attributes
    assert attributes.get(_semconv.A2A_TENANT) == expected
    assert_absent(
        only_span(exporter), _semconv.SERVER_ADDRESS, _semconv.SERVER_PORT
    )


@pytest.mark.parametrize('side', ['client', 'server'])
@pytest.mark.parametrize(
    ('reference_task_ids', 'description'),
    [(['t-0'], 'Answers questions.'), ([], '')],
    ids=['set', 'empty-values-absent'],
)
@pytest.mark.asyncio
async def test_message_and_agent_card_attributes(
    exporter, make_client, make_handler, side, reference_task_ids, description
):
    """Message and agent card fields are reported, and empty ones omitted."""
    card = AgentCard(
        name='Remote',
        description=description,
        version='1.0',
        capabilities=AgentCapabilities(streaming=True),
    )
    request = send_request(reference_task_ids=reference_task_ids)
    if side == 'client':
        transport = AsyncMock(spec=ClientTransport)
        transport.send_message_streaming = streaming()
        async for _ in make_client(transport, card=card).send_message(request):
            pass
    else:
        handler = await make_handler(card=card)
        await handler.on_message_send(request, server_context())

    attributes = only_span(exporter).attributes
    assert attributes[_semconv.A2A_MESSAGE_ID] == 'm-1'
    assert attributes[_semconv.GEN_AI_AGENT_NAME] == 'Remote'
    assert attributes[_semconv.GEN_AI_AGENT_VERSION] == '1.0'
    assert attributes.get(_semconv.A2A_MESSAGE_REFERENCE_TASK_IDS) == (
        tuple(reference_task_ids) or None
    )
    assert attributes.get(_semconv.GEN_AI_AGENT_DESCRIPTION) == (
        description or None
    )


@pytest.mark.parametrize(
    'service_parameters', [None, {'x-api-key': 'k'}], ids=['none', 'existing']
)
@pytest.mark.asyncio
async def test_trace_context_is_propagated(
    exporter, make_client, service_parameters
):
    """The span goes out as traceparent and is current inside the transport."""
    sent = {}
    nested_parent = {}

    async def get_task(request, *, context=None):
        sent.update(context.service_parameters)
        nested_parent['id'] = trace.get_current_span().get_span_context()
        return task()

    transport = AsyncMock(spec=ClientTransport)
    transport.get_task = get_task

    await make_client(transport).get_task(
        GetTaskRequest(id='t-1'),
        context=ClientCallContext(service_parameters=service_parameters),
    )

    span_context = only_span(exporter).context
    assert sent['traceparent'].startswith(
        f'00-{span_context.trace_id:032x}-{span_context.span_id:016x}-'
    )
    if service_parameters:
        assert sent['x-api-key'] == 'k'
    assert nested_parent['id'].span_id == span_context.span_id


@pytest.mark.parametrize(
    ('error', 'expected_type'),
    [
        (TaskNotFoundError(), 'TASK_NOT_FOUND'),
        (A2AClientError('down'), 'a2a.client.errors.A2AClientError'),
    ],
    ids=['a2a-reason', 'class-name'],
)
@pytest.mark.asyncio
async def test_errors_set_status_and_error_type(
    exporter, make_client, error, expected_type
):
    """A failed call gets ERROR status and an error.type, but no events."""
    transport = AsyncMock(spec=ClientTransport)
    transport.get_task.side_effect = error

    with pytest.raises(type(error)):
        await make_client(transport).get_task(GetTaskRequest(id='t-1'))

    span = only_span(exporter)
    assert span.status.status_code is StatusCode.ERROR
    assert span.status.description
    assert span.attributes[_semconv.ERROR_TYPE] == expected_type
    assert not span.events


@pytest.mark.asyncio
async def test_server_error_sets_status_and_error_type(exporter, make_handler):
    """A failed server call gets ERROR status and the A2A error reason."""
    handler = await make_handler()

    with pytest.raises(TaskNotFoundError):
        await handler.on_get_task(
            GetTaskRequest(id='missing'), server_context()
        )

    span = only_span(exporter)
    assert span.status.status_code is StatusCode.ERROR
    assert span.attributes[_semconv.ERROR_TYPE] == 'TASK_NOT_FOUND'
    assert span.attributes[_semconv.A2A_TASK_ID] == 'missing'
    assert not span.events


@pytest.mark.asyncio
async def test_error_mid_stream_keeps_earlier_ids(exporter, make_client):
    """A stream failing midway keeps the IDs from events before the failure."""
    transport = AsyncMock(spec=ClientTransport)
    transport.send_message_streaming = streaming(
        status_event(TaskState.TASK_STATE_WORKING),
        error=A2AClientError('dropped'),
    )

    with pytest.raises(A2AClientError):
        async for _ in make_client(transport).send_message(send_request()):
            pass

    span = only_span(exporter)
    assert span.status.status_code is StatusCode.ERROR
    assert span.attributes[_semconv.A2A_TASK_ID] == 't-1'


@pytest.mark.parametrize(
    ('message_fields', 'response', 'absent'),
    [
        (
            {'task_id': 't-old', 'context_id': 'c-1'},
            task('t-new', 'c-1'),
            _semconv.A2A_TASK_ID,
        ),
        (
            {'task_id': 't-1', 'context_id': 'c-old'},
            task('t-1', 'c-new'),
            _semconv.GEN_AI_CONVERSATION_ID,
        ),
    ],
    ids=['task-id', 'context-id'],
)
@pytest.mark.asyncio
async def test_request_and_response_disagreeing_omit_the_attribute(
    exporter, make_client, message_fields, response, absent
):
    """An ID that differs between request and response is not reported."""
    transport = AsyncMock(spec=ClientTransport)
    transport.send_message.return_value = SendMessageResponse(task=response)
    client = make_client(transport, streaming=False)

    async for _ in client.send_message(send_request(**message_fields)):
        pass

    span = only_span(exporter)
    assert_absent(span, absent)
    agreed = {_semconv.A2A_TASK_ID, _semconv.GEN_AI_CONVERSATION_ID} - {absent}
    assert agreed <= set(span.attributes)


@pytest.mark.asyncio
async def test_stream_with_differing_task_ids_omits_the_task_id(
    exporter, make_client
):
    """A stream whose events name different tasks reports no task ID."""
    transport = AsyncMock(spec=ClientTransport)
    transport.send_message_streaming = streaming(
        StreamResponse(task=task('t-1', 'c-1')),
        StreamResponse(task=task('t-2', 'c-1')),
    )

    async for _ in make_client(transport).send_message(send_request()):
        pass

    span = only_span(exporter)
    assert_absent(span, _semconv.A2A_TASK_ID)
    assert span.attributes[_semconv.GEN_AI_CONVERSATION_ID] == 'c-1'


@pytest.mark.asyncio
async def test_failed_call_keeps_the_request_task_id(exporter, make_client):
    """A call that fails before any response still reports the request's ID."""
    transport = AsyncMock(spec=ClientTransport)
    transport.get_task.side_effect = TaskNotFoundError()

    with pytest.raises(TaskNotFoundError):
        await make_client(transport).get_task(GetTaskRequest(id='t-1'))

    assert only_span(exporter).attributes[_semconv.A2A_TASK_ID] == 't-1'


@pytest.mark.asyncio
async def test_success_leaves_status_unset(exporter, make_client):
    """A successful call leaves the status UNSET and sets no error.type."""
    transport = AsyncMock(spec=ClientTransport)
    transport.get_task.return_value = task()

    await make_client(transport).get_task(GetTaskRequest(id='t-1'))

    span = only_span(exporter)
    assert span.status.status_code is StatusCode.UNSET
    assert _semconv.ERROR_TYPE not in span.attributes


async def _timed_out_unary(client, context):
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(
            client.get_task(GetTaskRequest(id='t-1'), context=context),
            timeout=0.01,
        )


async def _cancelled_stream(client, context):
    async def drain():
        async for _ in client.send_message(send_request(), context=context):
            pass

    consumer = asyncio.create_task(drain())
    await asyncio.sleep(0.01)
    consumer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await consumer


async def _abandoned_stream(client, context):
    events = client.send_message(send_request(), context=context)
    await anext(events)
    await events.aclose()


@pytest.mark.parametrize(
    'stop', [_timed_out_unary, _cancelled_stream, _abandoned_stream]
)
@pytest.mark.asyncio
async def test_span_ends_cleanly_when_call_stops_early(
    exporter, make_client, stop
):
    """Timeouts, cancellation and early exit end the span without an error."""

    async def slow_task(request, *, context=None):
        await asyncio.sleep(10)

    async def slow_stream(request, *, context=None):
        yield status_event(TaskState.TASK_STATE_WORKING)
        await asyncio.sleep(10)

    transport = AsyncMock(spec=ClientTransport)
    transport.get_task = slow_task
    transport.send_message_streaming = slow_stream
    context = ClientCallContext()

    await stop(make_client(transport), context)

    assert only_span(exporter).status.status_code is StatusCode.UNSET
    assert otel_plugin._SPAN_KEY not in context.state


@pytest.mark.asyncio
async def test_cancelled_server_call_is_not_an_error(exporter, make_handler):
    """A cancelled server call ends its span without marking it ERROR."""
    handler = await make_handler(executor=SlowAgent())
    context = server_context()

    call = asyncio.create_task(handler.on_message_send(send_request(), context))
    await asyncio.sleep(0.05)
    call.cancel()
    with pytest.raises(asyncio.CancelledError):
        await call

    span = only_span(exporter)
    assert span.status.status_code is StatusCode.UNSET
    assert _semconv.ERROR_TYPE not in span.attributes
    assert otel_plugin._SPAN_KEY not in context.state


@pytest.mark.asyncio
async def test_server_enriches_the_transport_span(exporter, make_handler):
    """With a transport span current, it is reused, renamed and not ended."""
    handler = await make_handler()
    transport_tracer = TracerProvider()
    transport_tracer.add_span_processor(SimpleSpanProcessor(exporter))

    with transport_tracer.get_tracer('starlette').start_as_current_span(
        'POST /a2a', kind=SpanKind.SERVER
    ) as transport_span:
        await handler.on_message_send(send_request(), server_context())
        assert transport_span.is_recording()
        transport_span.set_attribute('http.response.status_code', 200)

    span = only_span(exporter)
    assert span.name == 'SendMessage'
    assert span.attributes[_semconv.A2A_METHOD_NAME] == 'SendMessage'
    assert span.attributes['http.response.status_code'] == 200


@pytest.mark.asyncio
async def test_server_creates_its_own_span_without_a_transport_span(
    exporter, make_handler
):
    """With no transport span current, the plugin starts and ends its own."""
    handler = await make_handler()

    await handler.on_message_send(send_request(), server_context())

    span = only_span(exporter)
    assert span.name == 'SendMessage'
    assert span.kind is SpanKind.SERVER
    assert span.parent is None
    assert_absent(span, _semconv.A2A_PROTOCOL_VERSION)


TRACE_ID, PARENT_SPAN_ID = (
    0x4BF92F3577B34DA6A3CE929D0E0E4736,
    0x00F067AA0BA902B7,
)
TRACEPARENT = f'00-{TRACE_ID:032x}-{PARENT_SPAN_ID:016x}-01'


def grpc_context_with(metadata):
    grpc_context = MagicMock()
    grpc_context.invocation_metadata.return_value = metadata
    return ServerCallContext(state={'grpc_context': grpc_context})


@pytest.mark.parametrize(
    'context',
    [
        server_context(headers={'traceparent': TRACEPARENT}),
        grpc_context_with([('traceparent', TRACEPARENT)]),
        grpc_context_with([('traceparent', TRACEPARENT.encode())]),
        grpc_context_with(
            [('trace-bin', b'\xff\xfe'), ('TraceParent', TRACEPARENT)]
        ),
    ],
    ids=['headers', 'grpc-str', 'grpc-bytes', 'grpc-binary-and-mixed-case'],
)
@pytest.mark.asyncio
async def test_server_span_continues_the_callers_trace(
    exporter, make_handler, context
):
    """The server span continues the caller's trace and records its version."""
    handler = await make_handler()

    await handler.on_message_send(send_request(), context)

    span = only_span(exporter)
    assert span.context.trace_id == TRACE_ID
    assert span.parent.span_id == PARENT_SPAN_ID
    assert span.attributes[_semconv.A2A_PROTOCOL_VERSION] == '1.0'


@pytest.mark.parametrize('metadata', [None, []], ids=['none', 'empty'])
@pytest.mark.asyncio
async def test_server_without_grpc_metadata_starts_a_root_span(
    exporter, make_handler, metadata
):
    """Missing gRPC metadata yields a new root span, not a failed call."""
    handler = await make_handler()

    await handler.on_message_send(send_request(), grpc_context_with(metadata))

    span = only_span(exporter)
    assert span.parent is None
    assert_absent(span, _semconv.A2A_PROTOCOL_VERSION)


@pytest.mark.asyncio
async def test_server_keeps_the_grpc_span_name(exporter, make_handler):
    """An enriched gRPC span keeps its name; only HTTP spans are renamed."""
    handler = await make_handler()
    transport_tracer = TracerProvider()
    transport_tracer.add_span_processor(SimpleSpanProcessor(exporter))
    name = '/a2a.v1.A2AService/SendMessage'

    with transport_tracer.get_tracer('grpc').start_as_current_span(
        name, kind=SpanKind.SERVER
    ):
        await handler.on_message_send(
            send_request(),
            ServerCallContext(state={'grpc_context': MagicMock()}),
        )

    span = only_span(exporter)
    assert span.name == name
    assert span.attributes[_semconv.A2A_METHOD_NAME] == 'SendMessage'
