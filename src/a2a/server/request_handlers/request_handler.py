from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator

from a2a.server.context import ServerCallContext
from a2a.server.events.event_queue import Event
from a2a.types.a2a_pb2 import (
    CancelTaskRequest,
    DeleteTaskPushNotificationConfigRequest,
    GetTaskPushNotificationConfigRequest,
    GetTaskRequest,
    ListTaskPushNotificationConfigRequest,
    ListTaskPushNotificationConfigResponse,
    Message,
    SendMessageRequest,
    SetTaskPushNotificationConfigRequest,
    SubscribeToTaskRequest,
    Task,
    TaskPushNotificationConfig,
)
from a2a.types.extras import UnsupportedOperationError
from a2a.utils.errors import ServerError


class RequestHandler(ABC):
    """A2A request handler interface.

    This interface defines the methods that an A2A server implementation must
    provide to handle incoming JSON-RPC requests.
    """

    @abstractmethod
    async def on_get_task(
        self,
        params: GetTaskRequest,
        context: ServerCallContext | None = None,
    ) -> Task | None:
        """Handles the 'tasks/get' method.

        Retrieves the state and history of a specific task.

        Args:
            params: Parameters specifying the task ID and optionally history length.
            context: Context provided by the server.

        Returns:
            The `Task` object if found, otherwise `None`.
        """

    @abstractmethod
    async def on_cancel_task(
        self,
        params: CancelTaskRequest,
        context: ServerCallContext | None = None,
    ) -> Task | None:
        """Handles the 'tasks/cancel' method.

        Requests the agent to cancel an ongoing task.

        Args:
            params: Parameters specifying the task ID.
            context: Context provided by the server.

        Returns:
            The `Task` object with its status updated to canceled, or `None` if the task was not found.
        """

    @abstractmethod
    async def on_message_send(
        self,
        params: SendMessageRequest,
        context: ServerCallContext | None = None,
    ) -> Task | Message:
        """Handles the 'message/send' method (non-streaming).

        Sends a message to the agent to create, continue, or restart a task,
        and waits for the final result (Task or Message).

        Args:
            params: Parameters including the message and configuration.
            context: Context provided by the server.

        Returns:
            The final `Task` object or a final `Message` object.
        """

    @abstractmethod
    async def on_message_send_stream(
        self,
        params: SendMessageRequest,
        context: ServerCallContext | None = None,
    ) -> AsyncGenerator[Event]:
        """Handles the 'message/stream' method (streaming).

        Sends a message to the agent and yields stream events as they are
        produced (Task updates, Message chunks, Artifact updates).

        Args:
            params: Parameters including the message and configuration.
            context: Context provided by the server.

        Yields:
            `Event` objects from the agent's execution.

        Raises:
             ServerError(UnsupportedOperationError): By default, if not implemented.
        """
        raise ServerError(error=UnsupportedOperationError())
        yield

    @abstractmethod
    async def on_set_task_push_notification_config(
        self,
        params: SetTaskPushNotificationConfigRequest,
        context: ServerCallContext | None = None,
    ) -> TaskPushNotificationConfig:
        """Handles the 'tasks/pushNotificationConfig/set' method.

        Sets or updates the push notification configuration for a task.

        Args:
            params: Parameters including the task ID and push notification configuration.
            context: Context provided by the server.

        Returns:
            The provided `TaskPushNotificationConfig` upon success.
        """

    @abstractmethod
    async def on_get_task_push_notification_config(
        self,
        params: GetTaskPushNotificationConfigRequest,
        context: ServerCallContext | None = None,
    ) -> TaskPushNotificationConfig:
        """Handles the 'tasks/pushNotificationConfig/get' method.

        Retrieves the current push notification configuration for a task.

        Args:
            params: Parameters including the task ID.
            context: Context provided by the server.

        Returns:
            The `TaskPushNotificationConfig` for the task.
        """

    @abstractmethod
    async def on_subscribe_to_task(
        self,
        params: SubscribeToTaskRequest,
        context: ServerCallContext | None = None,
    ) -> AsyncGenerator[Event]:
        """Handles the 'SubscribeToTask' method.

        Allows a client to subscribe to a running streaming task's event stream.

        Args:
            params: Parameters including the task ID.
            context: Context provided by the server.

        Yields:
             `Event` objects from the agent's ongoing execution for the specified task.

        Raises:
             ServerError(UnsupportedOperationError): By default, if not implemented.
        """
        raise ServerError(error=UnsupportedOperationError())
        yield

    @abstractmethod
    async def on_list_task_push_notification_config(
        self,
        params: ListTaskPushNotificationConfigRequest,
        context: ServerCallContext | None = None,
    ) -> ListTaskPushNotificationConfigResponse:
        """Handles the 'ListTaskPushNotificationConfig' method.

        Retrieves the current push notification configurations for a task.

        Args:
            params: Parameters including the task ID.
            context: Context provided by the server.

        Returns:
            The `list[TaskPushNotificationConfig]` for the task.
        """

    @abstractmethod
    async def on_delete_task_push_notification_config(
        self,
        params: DeleteTaskPushNotificationConfigRequest,
        context: ServerCallContext | None = None,
    ) -> None:
        """Handles the 'tasks/pushNotificationConfig/delete' method.

        Deletes a push notification configuration associated with a task.

        Args:
            params: Parameters including the task ID.
            context: Context provided by the server.

        Returns:
            None
        """
