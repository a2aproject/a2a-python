from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Generic, Literal, TypeAlias, TypeVar


if TYPE_CHECKING:
    from a2a.client.client import ClientCallContext

from a2a.types.a2a_pb2 import (  # noqa: TC001
    AgentCard,
    CancelTaskRequest,
    DeleteTaskPushNotificationConfigRequest,
    GetExtendedAgentCardRequest,
    GetTaskPushNotificationConfigRequest,
    GetTaskRequest,
    ListTaskPushNotificationConfigsRequest,
    ListTaskPushNotificationConfigsResponse,
    ListTasksRequest,
    ListTasksResponse,
    SendMessageRequest,
    SendMessageResponse,
    StreamResponse,
    SubscribeToTaskRequest,
    Task,
    TaskPushNotificationConfig,
)


M = TypeVar('M')
P = TypeVar('P')
R = TypeVar('R')


@dataclass
class ClientCallInput(Generic[M, P]):
    """Represents the method and its associated input arguments payload."""

    method: M
    value: P


@dataclass
class ClientCallResult(Generic[M, R]):
    """Represents the method and its associated result payload."""

    method: M
    value: R


@dataclass
class BeforeArgs(Generic[M, P, R]):
    """Arguments passed to the interceptor before a method call."""

    input: ClientCallInput[M, P]
    agent_card: AgentCard
    context: ClientCallContext | None = None
    early_return: ClientCallResult[M, R] | None = None


@dataclass
class AfterArgs(Generic[M, R]):
    """Arguments passed to the interceptor after a method call completes."""

    result: ClientCallResult[M, R]
    agent_card: AgentCard
    context: ClientCallContext | None = None
    early_return: bool = False


class ClientCallInterceptor(ABC, Generic[M, P, R]):
    """An abstract base class for client-side call interceptors.

    Interceptors can inspect and modify requests before they are sent,
    which is ideal for concerns like authentication, logging, or tracing.
    """

    @abstractmethod
    async def before(self, args: UnionBeforeArgs) -> None:
        """Invoked before transport method."""

    @abstractmethod
    async def after(self, args: UnionAfterArgs) -> None:
        """Invoked after transport method."""


UnionBeforeArgs: TypeAlias = (
    BeforeArgs[
        Literal['send_message'], 'SendMessageRequest', 'SendMessageResponse'
    ]
    | BeforeArgs[
        Literal['send_message_streaming'],
        'SendMessageRequest',
        'StreamResponse',
    ]
    | BeforeArgs[Literal['get_task'], 'GetTaskRequest', 'Task']
    | BeforeArgs[Literal['list_tasks'], 'ListTasksRequest', 'ListTasksResponse']
    | BeforeArgs[Literal['cancel_task'], 'CancelTaskRequest', 'Task']
    | BeforeArgs[
        Literal['create_task_push_notification_config'],
        'TaskPushNotificationConfig',
        'TaskPushNotificationConfig',
    ]
    | BeforeArgs[
        Literal['get_task_push_notification_config'],
        'GetTaskPushNotificationConfigRequest',
        'TaskPushNotificationConfig',
    ]
    | BeforeArgs[
        Literal['list_task_push_notification_configs'],
        'ListTaskPushNotificationConfigsRequest',
        'ListTaskPushNotificationConfigsResponse',
    ]
    | BeforeArgs[
        Literal['delete_task_push_notification_config'],
        'DeleteTaskPushNotificationConfigRequest',
        None,
    ]
    | BeforeArgs[
        Literal['subscribe'], 'SubscribeToTaskRequest', 'StreamResponse'
    ]
    | BeforeArgs[
        Literal['get_extended_agent_card'],
        'GetExtendedAgentCardRequest',
        'AgentCard',
    ]
)

UnionAfterArgs: TypeAlias = (
    AfterArgs[Literal['send_message'], 'SendMessageResponse']
    | AfterArgs[Literal['send_message_streaming'], 'StreamResponse']
    | AfterArgs[Literal['get_task'], 'Task']
    | AfterArgs[Literal['list_tasks'], 'ListTasksResponse']
    | AfterArgs[Literal['cancel_task'], 'Task']
    | AfterArgs[
        Literal['create_task_push_notification_config'],
        'TaskPushNotificationConfig',
    ]
    | AfterArgs[
        Literal['get_task_push_notification_config'],
        'TaskPushNotificationConfig',
    ]
    | AfterArgs[
        Literal['list_task_push_notification_configs'],
        'ListTaskPushNotificationConfigsResponse',
    ]
    | AfterArgs[Literal['delete_task_push_notification_config'], None]
    | AfterArgs[Literal['subscribe'], 'StreamResponse']
    | AfterArgs[Literal['get_extended_agent_card'], 'AgentCard']
)
