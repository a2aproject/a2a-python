"""Client tracing: one OpenTelemetry CLIENT span per A2A request.

The span follows the A2A semantic conventions for the attributes a client
plugin can see today. It is current only while the client runs a call (see
`ClientPlugin.run_client_call`), so HTTP and gRPC spans become its children,
while the caller's own work between stream events does not.

Known gaps:

- ``server.address``, ``server.port`` and ``a2a.protocol.version`` need the
  interface the client selected, which `ClientRequest` does not carry yet.
- The SDK's legacy tracing (`a2a.utils.telemetry`) adds its own spans around
  transport methods. Run with ``OTEL_INSTRUMENTATION_A2A_SDK_ENABLED=false``
  to get only the spans from this plugin.
- Register this plugin first. If a later plugin rejects the request in
  ``before_client_request``, no final hook runs and the span is not ended.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import httpx

from opentelemetry import propagate, trace
from opentelemetry.trace import SpanKind, StatusCode

from a2a import plugins
from a2a.types import (
    Message,
    SendMessageRequest,
    SendMessageResponse,
    StreamResponse,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatusUpdateEvent,
)
from a2a.utils.errors import A2A_ERROR_REASONS


try:
    import grpc
except ImportError:  # gRPC is an optional extra.
    grpc = None


if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from opentelemetry.util.types import AttributeValue

    from a2a.plugins import ClientRequest, ClientResponse


METHOD_NAMES = {
    'send_message': 'SendMessage',
    'send_message_streaming': 'SendStreamingMessage',
    'get_task': 'GetTask',
    'list_tasks': 'ListTasks',
    'cancel_task': 'CancelTask',
    'subscribe': 'SubscribeToTask',
    'create_task_push_notification_config': 'CreateTaskPushNotificationConfig',
    'get_task_push_notification_config': 'GetTaskPushNotificationConfig',
    'list_task_push_notification_configs': 'ListTaskPushNotificationConfigs',
    'delete_task_push_notification_config': 'DeleteTaskPushNotificationConfig',
    'get_extended_agent_card': 'GetExtendedAgentCard',
}
"""SDK method names mapped to the semconv ``a2a.method.name`` values."""

# The request field holding the task id, per method.
_TASK_ID_FIELDS = {
    'get_task': 'id',
    'cancel_task': 'id',
    'subscribe': 'id',
    'create_task_push_notification_config': 'task_id',
    'get_task_push_notification_config': 'task_id',
    'list_task_push_notification_configs': 'task_id',
    'delete_task_push_notification_config': 'task_id',
}

_STATE_KEY = 'samples.plugins.tracing'


def error_type(error: BaseException) -> str:
    """Returns the ``error.type`` value for an error raised by a call.

    In order: the A2A error reason, the HTTP status code or the gRPC status
    name of the transport error that caused it, the exception's full class
    name.
    """
    for cls in type(error).__mro__:
        if reason := A2A_ERROR_REASONS.get(cls):
            return reason
    cause = error.__cause__
    if isinstance(cause, httpx.HTTPStatusError):
        return str(cause.response.status_code)
    if grpc is not None and isinstance(cause, grpc.RpcError):
        return cause.code().name
    return f'{type(error).__module__}.{type(error).__qualname__}'


@dataclass
class _Call:
    """Per-request state: the span and the values seen so far."""

    span: trace.Span
    payload: Any
    task_ids: set[str] = field(default_factory=set)
    states: set[int] = field(default_factory=set)
    context_ids: set[str] = field(default_factory=set)

    def observe(self, payload: Any) -> None:
        if isinstance(payload, StreamResponse | SendMessageResponse):
            which = payload.WhichOneof('payload')
            payload = getattr(payload, which) if which else None
        if isinstance(payload, Task):
            self.add(payload.id, payload.context_id, payload.status.state)
        elif isinstance(payload, TaskStatusUpdateEvent):
            self.add(payload.task_id, payload.context_id, payload.status.state)
        elif isinstance(payload, TaskArtifactUpdateEvent | Message):
            self.add(payload.task_id, payload.context_id)

    def add(
        self, task_id: str, context_id: str, state: int | None = None
    ) -> None:
        if task_id:
            self.task_ids.add(task_id)
        if context_id:
            self.context_ids.add(context_id)
        if state is not None:
            self.states.add(state)


class ClientTracingPlugin(plugins.ClientPlugin):
    """Creates a CLIENT span per request and propagates it to the server."""

    name = 'client-tracing'

    def __init__(
        self, tracer_provider: trace.TracerProvider | None = None
    ) -> None:
        self._tracer = trace.get_tracer(
            __name__, tracer_provider=tracer_provider
        )

    async def before_client_request(self, request: ClientRequest) -> None:
        """Starts the span and adds ``traceparent`` to the outgoing headers."""
        span = self._tracer.start_span(
            METHOD_NAMES.get(request.method, request.method),
            kind=SpanKind.CLIENT,
            attributes=_request_attributes(request),
        )
        call = _Call(span, request.payload)
        if field_name := _TASK_ID_FIELDS.get(request.method):
            call.add(getattr(request.payload, field_name), '')
        elif isinstance(request.payload, SendMessageRequest):
            message = request.payload.message
            call.add(message.task_id, message.context_id)

        headers = dict(request.context.service_parameters or {})
        propagate.inject(headers, context=trace.set_span_in_context(span))
        # A copy of the context, so that neither the caller's headers nor
        # concurrent calls sharing the caller's context see this call's state.
        request.context = request.context.model_copy(
            update={
                'state': {**request.context.state, _STATE_KEY: call},
                'service_parameters': headers,
            }
        )

    async def run_client_call(
        self,
        request: ClientRequest,
        call: Callable[[], Awaitable[Any]],
    ) -> Any:
        """Makes the span current while the client runs the call."""
        state = request.context.state.get(_STATE_KEY)
        if state is None:
            return await call()
        with trace.use_span(
            state.span,
            end_on_exit=False,
            record_exception=False,
            set_status_on_exception=False,
        ):
            return await call()

    async def after_client_request(self, response: ClientResponse) -> None:
        """Collects task values and ends the span on the final call."""
        state = response.context.state.get(_STATE_KEY)
        if state is None:
            return
        if response.payload is not None:
            state.observe(response.payload)
        if not response.is_final:
            return

        span = state.span
        # The transport may fill in the tenant while sending, so read it now.
        if tenant := getattr(state.payload, 'tenant', ''):
            span.set_attribute('a2a.tenant', tenant)
        # Semconv: omit each of these when the call saw more than one value.
        if len(state.task_ids) == 1:
            span.set_attribute('a2a.task.id', next(iter(state.task_ids)))
        if len(state.states) == 1:
            span.set_attribute(
                'a2a.task.state', TaskState.Name(next(iter(state.states)))
            )
        if len(state.context_ids) == 1:
            span.set_attribute(
                'gen_ai.conversation.id', next(iter(state.context_ids))
            )
        if response.error is not None:
            span.set_attribute('error.type', error_type(response.error))
            span.set_status(StatusCode.ERROR, str(response.error))
        span.end()


def _request_attributes(request: ClientRequest) -> dict[str, AttributeValue]:
    attributes: dict[str, AttributeValue] = {
        'a2a.method.name': METHOD_NAMES.get(request.method, request.method),
    }
    card = request.agent_card
    attributes.update(
        (key, value)
        for key, value in (
            ('gen_ai.agent.name', card.name),
            ('gen_ai.agent.description', card.description),
            ('gen_ai.agent.version', card.version),
        )
        if value
    )
    payload = request.payload
    if isinstance(payload, SendMessageRequest):
        attributes['a2a.message.id'] = payload.message.message_id
        if payload.message.reference_task_ids:
            attributes['a2a.message.reference_task_ids'] = list(
                payload.message.reference_task_ids
            )
        # Semconv: only for SendMessage, and only when true.
        if (
            request.method == 'send_message'
            and payload.configuration.return_immediately
        ):
            attributes['a2a.operation.return_immediately'] = True
    return attributes
