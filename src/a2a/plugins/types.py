"""Argument types passed to plugin hooks.

Hooks return ``None``. Per-call state lives in ``context.state`` (server and
client) or ``PushRequest.state`` (push), which the SDK passes unchanged from a
before-hook to the matching after-hook.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from a2a.client.client import ClientCallContext
    from a2a.server.context import ServerCallContext
    from a2a.server.events.event_queue import Event
    from a2a.server.tasks.push_notification_sender import (
        PushNotificationEvent,
    )
    from a2a.types.a2a_pb2 import AgentCard, Task, TaskState


@dataclass
class ServerRequest:
    """An incoming request, passed to `ServerPlugin.before_agent_run`.

    Attributes:
        method: A2A RPC name, e.g. ``'SendMessage'`` or ``'GetTask'``.
        payload: The request proto, e.g. ``SendMessageRequest``.
        context: The call context. ``state['headers']`` holds the transport
            headers and ``state['a2a.transport']`` the transport name.
        agent_card: The agent card served by this handler.
    """

    method: str
    payload: Any
    context: ServerCallContext
    agent_card: AgentCard | None = None


@dataclass
class ServerResponse:
    """A handler result, passed to `ServerPlugin.after_agent_run`.

    For a unary call the hook runs once with ``is_final=True``. For a stream
    it runs once per event with ``is_stream_event=True, is_final=False`` and
    once more with ``is_final=True`` and ``payload=None`` when the stream
    ends, including on error or client disconnect.

    Attributes:
        method: A2A RPC name.
        payload: The handler result or stream event, ``None`` on error or
            stream end.
        context: The same call context passed to `before_agent_run`.
        error: The exception raised by the handler, if any.
        is_stream_event: Whether ``payload`` is one event of a stream.
        is_final: Whether this is the last call for the request.
    """

    method: str
    payload: Any
    context: ServerCallContext
    error: BaseException | None = None
    is_stream_event: bool = False
    is_final: bool = True


@dataclass
class ClientRequest:
    """An outgoing call, passed to `ClientPlugin.before_agent_call`.

    Attributes:
        method: A2A RPC name, matching the server side.
        payload: The request proto. Plugins may replace it.
        agent_card: The card of the remote agent.
        context: The call context. ``service_parameters`` holds the outgoing
            headers.
        transport: Protocol binding used for the call, e.g. ``'JSONRPC'``.
        url: URL of the selected agent interface.
    """

    method: str
    payload: Any
    agent_card: AgentCard
    context: ClientCallContext
    transport: str = ''
    url: str = ''


@dataclass
class ClientResponse:
    """A call result, passed to `ClientPlugin.after_agent_call`.

    Follows the same ``is_stream_event`` / ``is_final`` rules as
    `ServerResponse`.

    Attributes:
        method: A2A RPC name.
        payload: The response or stream event, ``None`` on error or stream end.
        context: The same call context passed to `before_agent_call`.
        error: The exception raised by the call, if any.
        is_stream_event: Whether ``payload`` is one event of a stream.
        is_final: Whether this is the last call for the request.
    """

    method: str
    payload: Any
    context: ClientCallContext
    error: BaseException | None = None
    is_stream_event: bool = False
    is_final: bool = True


@dataclass
class TaskEventInfo:
    """A successful task write, passed to `TaskObserverPlugin.on_task_event`.

    Attributes:
        task_id: ID of the task.
        from_state: Persisted state before the write, ``None`` when the write
            created the task.
        to_state: State after the write. Equal to ``from_state`` for writes
            that do not change the state, such as artifact updates.
        event: The event that caused the write, ``None`` when the SDK wrote
            the task without one.
        task: The task after the write. This is the SDK's live object;
            copy it if you keep it beyond the hook call.
        context: Call context of the request that produced the write.
    """

    task_id: str
    from_state: TaskState | None
    to_state: TaskState
    event: Event | None
    task: Task
    context: ServerCallContext


@dataclass
class PushRequest:
    """One push delivery, passed to `PushPlugin.before_push_send`.

    Attributes:
        task_id: ID of the task.
        url: Webhook URL.
        headers: Outgoing HTTP headers. Plugins may add or change entries.
        event: The event being pushed.
        skip: Set to ``True`` to drop this delivery.
        state: Per-delivery plugin state, available again in `PushResult`.
    """

    task_id: str
    url: str
    headers: dict[str, str]
    event: PushNotificationEvent
    skip: bool = False
    state: dict[str, Any] = field(default_factory=dict)


@dataclass
class PushResult:
    """The outcome of one push delivery, passed to `PushPlugin.on_push_sent`.

    Attributes:
        request: The `PushRequest` for this delivery.
        duration_s: Duration of the HTTP request in seconds.
        status_code: HTTP status code, ``None`` if no response was received.
        error: The exception if delivery failed, ``None`` on success.
        skipped: Whether a plugin dropped the delivery.
    """

    request: PushRequest
    duration_s: float
    status_code: int | None
    error: BaseException | None
    skipped: bool = False
