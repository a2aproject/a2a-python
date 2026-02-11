from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator, Callable

from a2a.client.middleware import ClientCallContext
from a2a.types import (
    AgentCard,
    GetTaskPushNotificationConfigParams,
    Message,
    MessageSendParams,
    Task,
    TaskArtifactUpdateEvent,
    TaskIdParams,
    TaskPushNotificationConfig,
    TaskQueryParams,
    TaskStatusUpdateEvent,
)


class ClientTransport(ABC):
    """Abstract base class for a client transport."""

    async def __aenter__(self) -> ClientTransport:
        """Enter the async context manager, returning the transport itself."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object | None,
    ) -> None:
        """Exit the async context manager, ensuring close() is called."""
        await self.close()

    @abstractmethod
    async def send_message(
        self,
        request: MessageSendParams,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> Task | Message:
        """Sends a non-streaming message request to the agent."""

    @abstractmethod
    async def send_message_streaming(
        self,
        request: MessageSendParams,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> AsyncGenerator[
        Message | Task | TaskStatusUpdateEvent | TaskArtifactUpdateEvent
    ]:
        """Sends a streaming message request to the agent and yields responses as they arrive."""
        return
        yield

    @abstractmethod
    async def get_task(
        self,
        request: TaskQueryParams,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> Task:
        """Retrieves the current state and history of a specific task."""

    @abstractmethod
    async def cancel_task(
        self,
        request: TaskIdParams,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> Task:
        """Requests the agent to cancel a specific task."""

    @abstractmethod
    async def set_task_callback(
        self,
        request: TaskPushNotificationConfig,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> TaskPushNotificationConfig:
        """Sets or updates the push notification configuration for a specific task."""

    @abstractmethod
    async def get_task_callback(
        self,
        request: GetTaskPushNotificationConfigParams,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> TaskPushNotificationConfig:
        """Retrieves the push notification configuration for a specific task."""

    @abstractmethod
    async def resubscribe(
        self,
        request: TaskIdParams,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> AsyncGenerator[
        Task | Message | TaskStatusUpdateEvent | TaskArtifactUpdateEvent
    ]:
        """Reconnects to get task updates."""
        return
        yield

    @abstractmethod
    async def get_card(
        self,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
        signature_verifier: Callable[[AgentCard], None] | None = None,
    ) -> AgentCard:
        """Retrieves the AgentCard."""

    @abstractmethod
    async def close(self) -> None:
        """Closes the transport."""
