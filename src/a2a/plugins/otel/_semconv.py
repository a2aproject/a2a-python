"""A2A semantic conventions: attribute keys and value mapping."""

from __future__ import annotations

from urllib.parse import urlparse

from a2a.types.a2a_pb2 import TaskState
from a2a.utils.errors import A2A_ERROR_REASONS, A2AError


A2A_METHOD_NAME = 'a2a.method.name'
A2A_PROTOCOL_VERSION = 'a2a.protocol.version'
A2A_TENANT = 'a2a.tenant'
A2A_MESSAGE_ID = 'a2a.message.id'
A2A_MESSAGE_REFERENCE_TASK_IDS = 'a2a.message.reference_task_ids'
A2A_TASK_ID = 'a2a.task.id'
A2A_TASK_STATE = 'a2a.task.state'
GEN_AI_CONVERSATION_ID = 'gen_ai.conversation.id'
GEN_AI_AGENT_NAME = 'gen_ai.agent.name'
GEN_AI_AGENT_DESCRIPTION = 'gen_ai.agent.description'
GEN_AI_AGENT_VERSION = 'gen_ai.agent.version'
ERROR_TYPE = 'error.type'
SERVER_ADDRESS = 'server.address'
SERVER_PORT = 'server.port'

METHOD_NAMES = {
    'send_message': 'SendMessage',
    'send_message_streaming': 'SendStreamingMessage',
    'get_task': 'GetTask',
    'cancel_task': 'CancelTask',
    'subscribe': 'SubscribeToTask',
    'list_tasks': 'ListTasks',
    'create_task_push_notification_config': (
        'CreateTaskPushNotificationConfig'
    ),
    'get_task_push_notification_config': 'GetTaskPushNotificationConfig',
    'list_task_push_notification_configs': ('ListTaskPushNotificationConfigs'),
    'delete_task_push_notification_config': (
        'DeleteTaskPushNotificationConfig'
    ),
    'get_extended_agent_card': 'GetExtendedAgentCard',
}


def task_state_name(state: int) -> str | None:
    """Returns the conventions' name for a `TaskState`, or None if unset."""
    if state == TaskState.TASK_STATE_UNSPECIFIED:
        return None
    return TaskState.Name(state)


def error_type(error: BaseException) -> str:
    """Returns the `error.type` value for an exception.

    Prefers the A2A error reason, and falls back to the canonical name of the
    exception.
    """
    if isinstance(error, A2AError) and (
        reason := A2A_ERROR_REASONS.get(type(error))
    ):
        return reason
    return f'{type(error).__module__}.{type(error).__qualname__}'


def host_port(url: str) -> tuple[str | None, int | None]:
    """Splits an interface URL into its `server.address` and `server.port`."""
    if not url:
        return None, None
    parsed = urlparse(url)
    return parsed.hostname, parsed.port
