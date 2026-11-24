"""The OpenTelemetry plugin."""

from __future__ import annotations

import logging
import os

from importlib.metadata import version
from typing import TYPE_CHECKING, Any

from a2a.plugins.base import CompletePlugin
from a2a.plugins.otel import _semconv
from a2a.types.a2a_pb2 import SendMessageResponse, StreamResponse, Task
from a2a.utils.experimental import experimental


if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from contextlib import AbstractContextManager

    from opentelemetry.trace import Span, Tracer, TracerProvider

    from a2a.plugins.types import ClientRequest, ClientResponse
    from a2a.types.a2a_pb2 import AgentInterface


logger = logging.getLogger(__name__)

try:
    from opentelemetry import trace
    from opentelemetry.propagate import inject
    from opentelemetry.trace import SpanKind, StatusCode

    _otel_installed = True
except ImportError:
    _otel_installed = False


ENABLED_ENV_VAR = 'A2A_OTEL_PLUGIN_ENABLED'

INSTRUMENTING_MODULE_NAME = 'a2a-python-sdk.otel'

_SPAN_KEY = 'a2a.otel.span'

# Task and context IDs of a ListTasks call describe the listed tasks, not the
# operation, so they are never reported for it.
_SKIP_TASK_ATTRIBUTES = frozenset({'list_tasks'})

# Methods whose request `id` is the task ID. Elsewhere `id` can name something
# else: f.e config's ID
_ID_IS_TASK_ID = frozenset({'get_task', 'cancel_task', 'subscribe'})


def _current(span: Span) -> AbstractContextManager[Span]:
    """Makes the span current without letting OTel set its status or events."""
    return trace.use_span(
        span,
        end_on_exit=False,
        record_exception=False,
        set_status_on_exception=False,
    )


def _env_enabled() -> bool:
    return os.getenv(ENABLED_ENV_VAR, 'true').lower() == 'true'


def _sdk_version() -> str:
    return version('a2a-sdk')


@experimental
class OpenTelemetryPlugin(CompletePlugin):
    """Reports A2A client and server calls as OpenTelemetry spans.

    Register it before creating clients and request handlers::

        from a2a import plugins
        from a2a.plugins.otel import OpenTelemetryPlugin

        plugins.set_global(plugins.sequential(OpenTelemetryPlugin()))

    The application configures the OpenTelemetry SDK and its exporter; this
    plugin only creates spans. Set ``A2A_OTEL_PLUGIN_ENABLED=false`` to turn
    it into a no-op without removing it.
    """

    name = 'opentelemetry'

    def __init__(self, tracer_provider: TracerProvider | None = None) -> None:
        """Initializes the plugin.

        Args:
            tracer_provider: Provider the tracer is taken from. Defaults to
                the global provider, which the application configures.

        Raises:
            ImportError: If OpenTelemetry is not installed. Asking for this
                plugin states intent, so a silent no-op would hide the
                missing dependency.
        """
        if not _otel_installed:
            raise ImportError(
                'OpenTelemetryPlugin requires OpenTelemetry. Install it '
                'with: \'pip install "a2a-sdk[telemetry]"\''
            )

        self._enabled = _env_enabled()
        if not self._enabled:
            logger.debug(
                'A2A OpenTelemetry plugin disabled via %s. No spans will '
                'be reported.',
                ENABLED_ENV_VAR,
            )
            return

        provider: Any = tracer_provider if tracer_provider else trace
        self._tracer: Tracer = provider.get_tracer(
            INSTRUMENTING_MODULE_NAME, _sdk_version()
        )

    async def before_client_request(self, request: ClientRequest) -> None:
        """Starts the client span and propagates it to the agent."""
        if not self._enabled:
            return

        method = _semconv.get_method_name(request.method)
        span = self._tracer.start_span(method, kind=SpanKind.CLIENT)
        request.context.state[_SPAN_KEY] = span

        span.set_attribute(_semconv.A2A_METHOD_NAME, method)
        _set_request_attributes(span, request)

        carrier = dict(request.context.service_parameters or {})
        with _current(span):
            inject(carrier)
        request.context.service_parameters = carrier

    async def wrap_client_call(
        self, request: ClientRequest, call: Callable[[], Awaitable[Any]]
    ) -> Any:
        """Runs the call with the span current, so transport spans nest."""
        span = request.context.state.get(_SPAN_KEY)
        if span is None:
            return await call()
        with _current(span):
            return await call()

    async def after_client_request(self, response: ClientResponse) -> None:
        """Records what the response carries, and ends the span when final."""
        if not self._enabled:
            return

        span = response.context.state.get(_SPAN_KEY)
        if span is None:
            return

        _set_response_attributes(span, response)
        if not response.is_final:
            return

        del response.context.state[_SPAN_KEY]
        if response.error is not None:
            span.set_status(StatusCode.ERROR, str(response.error))
            span.set_attribute(
                _semconv.ERROR_TYPE, _semconv.error_type(response.error)
            )
        span.end()


def _set_request_attributes(span: Span, request: ClientRequest) -> None:
    """Sets the attributes known before the request is sent."""
    card = request.agent_card
    if card.name:
        span.set_attribute(_semconv.GEN_AI_AGENT_NAME, card.name)
    if card.description:
        span.set_attribute(_semconv.GEN_AI_AGENT_DESCRIPTION, card.description)
    if card.version:
        span.set_attribute(_semconv.GEN_AI_AGENT_VERSION, card.version)

    payload = request.payload
    message = getattr(payload, 'message', None)
    if message is not None and message.message_id:
        span.set_attribute(_semconv.A2A_MESSAGE_ID, message.message_id)
        if message.reference_task_ids:
            span.set_attribute(
                _semconv.A2A_MESSAGE_REFERENCE_TASK_IDS,
                list(message.reference_task_ids),
            )

    if request.method not in _SKIP_TASK_ATTRIBUTES:
        _set_ids(span, payload, request.method)
        if message is not None:
            _set_ids(span, message, request.method)

    interface = request.interface
    tenant = getattr(payload, 'tenant', '') or (
        interface.tenant if interface else ''
    )
    if tenant:
        span.set_attribute(_semconv.A2A_TENANT, tenant)
    if interface is not None:
        _set_interface_attributes(span, interface)


def _set_interface_attributes(span: Span, interface: AgentInterface) -> None:
    """Sets the attributes taken from the selected Agent Card interface."""
    if interface.protocol_version:
        span.set_attribute(
            _semconv.A2A_PROTOCOL_VERSION, interface.protocol_version
        )
    address, port = _semconv.host_port(interface.url)
    if address:
        span.set_attribute(_semconv.SERVER_ADDRESS, address)
    if port is not None:
        span.set_attribute(_semconv.SERVER_PORT, port)


def _set_response_attributes(span: Span, response: ClientResponse) -> None:
    """Sets the task attributes a response payload carries.

    The task state is reported for unary calls only: a stream moves through
    several states, which no single value describes.
    """
    if response.payload is None or response.method in _SKIP_TASK_ATTRIBUTES:
        return
    payload = _unwrap(response.payload)
    _set_ids(span, payload, response.method)
    if response.is_stream_event:
        return
    status = getattr(payload, 'status', None)
    if status is not None and (state := _semconv.task_state_name(status.state)):
        span.set_attribute(_semconv.A2A_TASK_STATE, state)


def _unwrap(payload: Any) -> Any:
    """Returns the task, message or event a response wraps."""
    if isinstance(payload, (SendMessageResponse, StreamResponse)):
        which = payload.WhichOneof('payload')
        return getattr(payload, which) if which else payload
    return payload


def _set_ids(span: Span, payload: Any, method: str) -> None:
    """Sets the task and context IDs a payload carries."""
    if task_id := _task_id(payload, method):
        span.set_attribute(_semconv.A2A_TASK_ID, task_id)
    if context_id := getattr(payload, 'context_id', ''):
        span.set_attribute(_semconv.GEN_AI_CONVERSATION_ID, context_id)


def _task_id(payload: Any, method: str) -> str:
    """Returns the task ID a payload carries, or ''.

    Most shapes name it `task_id`. A `Task` and the requests of the methods in
    `_ID_IS_TASK_ID` name it `id`, which elsewhere can identify something else.
    """
    if task_id := getattr(payload, 'task_id', ''):
        return task_id
    if isinstance(payload, Task) or method in _ID_IS_TASK_ID:
        return getattr(payload, 'id', '')
    return ''
