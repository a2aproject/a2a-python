"""The OpenTelemetry plugin."""

from __future__ import annotations

import logging
import os

from asyncio import CancelledError
from dataclasses import dataclass, field
from importlib.metadata import version
from typing import TYPE_CHECKING, Any

from a2a.plugins.base import CompletePlugin
from a2a.plugins.otel import _semconv
from a2a.types.a2a_pb2 import SendMessageResponse, StreamResponse, Task
from a2a.utils import telemetry as legacy_telemetry
from a2a.utils.constants import VERSION_HEADER
from a2a.utils.experimental import experimental


if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable, Mapping
    from contextlib import AbstractContextManager

    from opentelemetry.context import Context
    from opentelemetry.trace import Span, Tracer, TracerProvider

    from a2a.plugins.types import (
        ClientRequest,
        ClientResponse,
        ServerRequest,
        ServerResponse,
    )
    from a2a.types.a2a_pb2 import AgentCard, AgentInterface, Message


logger = logging.getLogger(__name__)

try:
    from opentelemetry import trace
    from opentelemetry.propagate import extract, inject
    from opentelemetry.trace import SpanKind, StatusCode

    _otel_installed = True
except ImportError:
    _otel_installed = False


ENABLED_ENV_VAR = 'A2A_OTEL_PLUGIN_ENABLED'

INSTRUMENTING_MODULE_NAME = 'a2a-python-sdk.otel'

_SDK_VERSION = version('a2a-sdk')

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


@dataclass
class _UniqueValue:
    """Tracks whether a call carried exactly one distinct value."""

    value: Any = None
    is_unique: bool = True

    def add(self, value: Any) -> None:
        """Records a value, skipping empty ones (protobuf unsets are '')."""
        if not value:
            return
        if self.value is None:
            self.value = value
        elif value != self.value:
            self.is_unique = False

    def get(self) -> Any:
        """Returns the single value seen, or None if none or several were."""
        return self.value if self.is_unique else None


@dataclass
class _CallState:
    """A call's span and the attributes the conventions omit when ambiguous."""

    span: Span
    is_owned: bool = True
    task_id: _UniqueValue = field(default_factory=_UniqueValue)
    context_id: _UniqueValue = field(default_factory=_UniqueValue)
    task_state: _UniqueValue = field(default_factory=_UniqueValue)

    def set(self, key: str, value: Any) -> None:
        """Sets an attribute, unless the span has already ended."""
        if self.span.is_recording():
            self.span.set_attribute(key, value)

    def end(self) -> None:
        """Sets the attributes left with a single value, then ends the span."""
        for key, tracked in (
            (_semconv.A2A_TASK_ID, self.task_id),
            (_semconv.GEN_AI_CONVERSATION_ID, self.context_id),
            (_semconv.A2A_TASK_STATE, self.task_state),
        ):
            if (value := tracked.get()) is not None:
                self.set(key, value)
        if self.is_owned:  ## Can't close the span that server doesn't own
            self.span.end()


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

        if legacy_telemetry.otel_enabled:
            legacy_telemetry.disable_tracing()
            logger.warning(
                'Legacy A2A tracing is active. Turning it off so calls are '
                'not traced twice. Set %s=false to silence this.',
                legacy_telemetry.ENABLED_ENV_VAR,
            )

        provider: Any = tracer_provider if tracer_provider else trace
        self._tracer: Tracer = provider.get_tracer(
            INSTRUMENTING_MODULE_NAME, _SDK_VERSION
        )

    async def before_client_request(self, request: ClientRequest) -> None:
        """Starts the client span and propagates it to the agent."""
        if not self._enabled:
            return

        method = _semconv.get_method_name(request.method)
        span = self._tracer.start_span(method, kind=SpanKind.CLIENT)
        state = _CallState(span)
        request.context.state[_SPAN_KEY] = state

        state.set(_semconv.A2A_METHOD_NAME, method)
        _set_client_request_attributes(state, request)

        carrier = dict(request.context.service_parameters or {})
        with _current(span):
            inject(carrier)
        request.context.service_parameters = carrier

    async def wrap_client_call(
        self, request: ClientRequest, call: Callable[[], Awaitable[Any]]
    ) -> Any:
        """Runs the call with the span current, so transport spans nest."""
        state = request.context.state.get(_SPAN_KEY)
        if state is None:
            return await call()
        with _current(state.span):
            return await call()

    async def after_client_request(self, response: ClientResponse) -> None:
        """Records what the response carries, and ends the span when final."""
        if not self._enabled:
            return

        state = response.context.state.get(_SPAN_KEY)
        if state is None:
            return

        _collect_response(state, response)
        if not response.is_final:
            return

        del response.context.state[_SPAN_KEY]
        _finish(state, response.error)

    async def before_handler_run(self, request: ServerRequest) -> None:
        """Enriches the transport's server span, or starts one if none."""
        if not self._enabled:
            return

        method = _semconv.get_method_name(request.method)
        current = trace.get_current_span()
        if transport := _transport_kind(current):
            state = _CallState(current, is_owned=False)
            if transport == 'http':
                current.update_name(method)
        else:
            state = _CallState(
                self._tracer.start_span(
                    method,
                    kind=SpanKind.SERVER,
                    context=_parent_context(request),
                )
            )
        request.context.state[_SPAN_KEY] = state

        state.set(_semconv.A2A_METHOD_NAME, method)
        _set_server_request_attributes(state, request)

    async def wrap_server_call(
        self, request: ServerRequest, call: Callable[[], Awaitable[Any]]
    ) -> Any:
        """Runs the handler with the plugin's own span current."""
        state = request.context.state.get(_SPAN_KEY)
        if state is None or not state.is_owned:
            return await call()
        with _current(state.span):
            return await call()

    async def after_handler_run(self, response: ServerResponse) -> None:
        """Records what the handler returned, and ends the span when final."""
        if not self._enabled:
            return

        state = response.context.state.get(_SPAN_KEY)
        if state is None:
            return

        _collect_response(state, response)
        if not response.is_final:
            return

        del response.context.state[_SPAN_KEY]
        error = response.error
        _finish(state, None if isinstance(error, CancelledError) else error)


def _finish(state: _CallState, error: BaseException | None) -> None:
    """Records the error, if any, and ends the call's span."""
    if error is not None:
        if state.span.is_recording():
            state.span.set_status(StatusCode.ERROR, str(error))
        state.set(_semconv.ERROR_TYPE, _semconv.error_type(error))
    state.end()


def _transport_kind(span: Span) -> str | None:
    """Returns 'http' or 'grpc' for a transport server span, else None."""
    if not span.is_recording() or getattr(span, 'kind', None) is not (
        SpanKind.SERVER
    ):
        return None
    attributes = getattr(span, 'attributes', None) or {}
    if (
        attributes.get('rpc.system.name', attributes.get('rpc.system'))
        == 'grpc'
    ):
        return 'grpc'
    if 'http.request.method' in attributes or 'http.method' in attributes:
        return 'http'
    return None


def _parent_context(request: ServerRequest) -> Context | None:
    """Returns the caller's trace context, read from the incoming request.

    Returns None when a span is already current, so the new span nests under
    it, or when the request carries no trace context.
    """
    if trace.get_current_span().get_span_context().is_valid:
        return None
    state = request.context.state
    if headers := state.get('headers'):
        return extract(headers)
    if grpc_context := state.get('grpc_context'):
        return extract(_grpc_carrier(grpc_context.invocation_metadata()))
    return None


def _grpc_carrier(
    metadata: Iterable[tuple[str, str | bytes]] | None,
) -> dict[str, str]:
    """Returns gRPC metadata as a string carrier the propagator can read.

    Metadata may be missing, and binary values may not be UTF-8, so those are
    skipped rather than raised on
    """
    carrier: dict[str, str] = {}
    for key, raw in metadata or ():
        if isinstance(raw, bytes):
            try:
                value = raw.decode('utf-8')
            except UnicodeDecodeError:
                continue
        else:
            value = raw
        carrier[key.lower()] = value
    return carrier


def _set_request_attributes(
    state: _CallState, request: ClientRequest | ServerRequest
) -> None:
    """Sets the attributes both sides read from a request."""
    if request.agent_card is not None:
        _set_agent_attributes(state, request.agent_card)

    payload = request.payload
    message = getattr(payload, 'message', None)
    if message is not None:
        _set_message_attributes(state, message)

    if request.method not in _SKIP_TASK_ATTRIBUTES:
        _collect_ids(state, payload, request.method)
        if message is not None:
            _collect_ids(state, message, request.method)


def _set_client_request_attributes(
    state: _CallState, request: ClientRequest
) -> None:
    """Sets the attributes of an outgoing request."""
    _set_request_attributes(state, request)
    interface = request.interface
    tenant = getattr(request.payload, 'tenant', '') or (
        interface.tenant if interface else ''
    )
    if tenant:
        state.set(_semconv.A2A_TENANT, tenant)
    if interface is not None:
        _set_interface_attributes(state, interface)


def _set_server_request_attributes(
    state: _CallState, request: ServerRequest
) -> None:
    """Sets the attributes of an incoming request."""
    _set_request_attributes(state, request)
    if request.context.tenant:
        state.set(_semconv.A2A_TENANT, request.context.tenant)
    if version := _requested_version(request.context.state):
        state.set(_semconv.A2A_PROTOCOL_VERSION, version)


def _requested_version(state: Mapping[str, Any]) -> str | None:
    """Returns the protocol version the caller sent."""
    key = VERSION_HEADER.lower()
    if headers := state.get('headers'):
        return headers.get(key) or headers.get(VERSION_HEADER)
    if grpc_context := state.get('grpc_context'):
        return _grpc_carrier(grpc_context.invocation_metadata()).get(key)
    return None


def _set_agent_attributes(state: _CallState, card: AgentCard) -> None:
    """Sets the attributes taken from the agent card."""
    if card.name:
        state.set(_semconv.GEN_AI_AGENT_NAME, card.name)
    if card.description:
        state.set(_semconv.GEN_AI_AGENT_DESCRIPTION, card.description)
    if card.version:
        state.set(_semconv.GEN_AI_AGENT_VERSION, card.version)


def _set_message_attributes(state: _CallState, message: Message) -> None:
    """Sets the attributes taken from the request message."""
    if not message.message_id:
        return
    state.set(_semconv.A2A_MESSAGE_ID, message.message_id)
    if message.reference_task_ids:
        state.set(
            _semconv.A2A_MESSAGE_REFERENCE_TASK_IDS,
            list(message.reference_task_ids),
        )


def _set_interface_attributes(
    state: _CallState, interface: AgentInterface
) -> None:
    """Sets the attributes taken from the selected Agent Card interface."""
    if interface.protocol_version:
        state.set(_semconv.A2A_PROTOCOL_VERSION, interface.protocol_version)
    address, port = _semconv.host_port(interface.url)
    if address:
        state.set(_semconv.SERVER_ADDRESS, address)
    if port is not None:
        state.set(_semconv.SERVER_PORT, port)


def _collect_response(
    state: _CallState, response: ClientResponse | ServerResponse
) -> None:
    """Records the task attributes a response payload carries.

    The task state is recorded for unary calls only: a stream moves through
    several states, which no single value describes.
    """
    if response.payload is None or response.method in _SKIP_TASK_ATTRIBUTES:
        return
    payload = _unwrap(response.payload)
    _collect_ids(state, payload, response.method)
    if response.is_stream_event:
        return
    status = getattr(payload, 'status', None)
    if status is not None:
        state.task_state.add(_semconv.task_state_name(status.state))


def _unwrap(payload: Any) -> Any:
    """Returns the task, message or event a response wraps."""
    if isinstance(payload, (SendMessageResponse, StreamResponse)):
        which = payload.WhichOneof('payload')
        return getattr(payload, which) if which else payload
    return payload


def _collect_ids(state: _CallState, payload: Any, method: str) -> None:
    """Records the task and context IDs a payload carries."""
    state.task_id.add(_task_id(payload, method))
    state.context_id.add(getattr(payload, 'context_id', ''))


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
