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
from a2a.plugins import ClientRequest, ClientResponse
from a2a.plugins.otel import (
    OpenTelemetryPlugin,
    _semconv,
    plugin as otel_plugin,
)
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
    assert version == otel_plugin._sdk_version()


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

    def make(transport, *, card=CARD, interface=INTERFACE):
        return BaseClient(
            card,
            ClientConfig(streaming=True),
            transport,
            [],
            interface=interface,
            plugins=plugins.sequential(
                otel_plugin.OpenTelemetryPlugin(provider)
            ),
        )

    return make


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
    exporter, make_client, state, expected
):
    """A unary call reports the task ID, context ID and a set task state."""
    transport = AsyncMock(spec=ClientTransport)
    transport.get_task.return_value = task(state=state)

    await make_client(transport).get_task(GetTaskRequest(id='t-1'))

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
        [],
    ],
    ids=['state-changes', 'no-events'],
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


@pytest.mark.parametrize('count', [0, 1, 3])
@pytest.mark.asyncio
async def test_list_tasks_never_reports_task_ids(exporter, make_client, count):
    """ListTasks reports no task attributes, whatever the number of tasks."""
    transport = AsyncMock(spec=ClientTransport)
    transport.list_tasks.return_value = ListTasksResponse(
        tasks=[task(f't-{i}', f'c-{i}') for i in range(count)]
    )

    await make_client(transport).list_tasks(
        ListTasksRequest(context_id='filter')
    )

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
    ('reference_task_ids', 'description'),
    [(['t-0'], 'Answers questions.'), ([], '')],
    ids=['set', 'empty-values-absent'],
)
@pytest.mark.asyncio
async def test_message_and_agent_card_attributes(
    exporter, make_client, reference_task_ids, description
):
    """Message and agent card fields are reported, and empty ones omitted."""
    transport = AsyncMock(spec=ClientTransport)
    transport.send_message_streaming = streaming()
    card = AgentCard(
        name='Remote',
        description=description,
        version='1.0',
        capabilities=AgentCapabilities(streaming=True),
    )

    async for _ in make_client(transport, card=card).send_message(
        send_request(reference_task_ids=reference_task_ids)
    ):
        pass

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
