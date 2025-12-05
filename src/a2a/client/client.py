import dataclasses
import logging

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Callable, Coroutine
from typing import Any

import httpx

from a2a.client.middleware import ClientCallContext, ClientCallInterceptor
from a2a.client.optionals import Channel
from a2a.types import (
    AgentCard,
    GetTaskPushNotificationConfigParams,
    Message,
    PushNotificationConfig,
    Task,
    TaskArtifactUpdateEvent,
    TaskIdParams,
    TaskPushNotificationConfig,
    TaskQueryParams,
    TaskStatusUpdateEvent,
    TransportProtocol,
)


logger = logging.getLogger(__name__)


@dataclasses.dataclass
class ClientConfig:
    """Configuration class for the A2AClient Factory."""

    streaming: bool = True
    """Whether client supports streaming"""

    polling: bool = False
    """Whether client prefers to poll for updates from message:send. It is
    the callers job to check if the response is completed and if not run a
    polling loop."""

    httpx_client: httpx.AsyncClient | None = None
    """Http client to use to connect to agent."""

    grpc_channel_factory: Callable[[str], Channel] | None = None
    """Generates a grpc connection channel for a given url."""

    supported_transports: list[TransportProtocol | str] = dataclasses.field(
        default_factory=list
    )
    """Ordered list of transports for connecting to agent
       (in order of preference). Empty implies JSONRPC only.

       This is a string type to allow custom
       transports to exist in closed ecosystems.
    """

    use_client_preference: bool = False
    """Whether to use client transport preferences over server preferences.
       Recommended to use server preferences in most situations."""

    accepted_output_modes: list[str] = dataclasses.field(default_factory=list)
    """The set of accepted output modes for the client."""

    push_notification_configs: list[PushNotificationConfig] = dataclasses.field(
        default_factory=list
    )
    """Push notification callbacks to use for every request."""

    extensions: list[str] = dataclasses.field(default_factory=list)
    """A list of extension URIs the client supports."""


UpdateEvent = TaskStatusUpdateEvent | TaskArtifactUpdateEvent | None
# Alias for emitted events from client
ClientEvent = tuple[Task, UpdateEvent]
# Alias for an event consuming callback. It takes either a (task, update) pair
# or a message as well as the agent card for the agent this came from.
Consumer = Callable[
    [ClientEvent | Message, AgentCard], Coroutine[None, Any, Any]
]


class Client(ABC):
    """Abstract base class defining the interface for an A2A client.

    This class provides a standard set of methods for interacting with an A2A
    agent, regardless of the underlying transport protocol (e.g., gRPC, JSON-RPC).
    It supports sending messages, managing tasks, and handling event streams.
    """

    def __init__(
        self,
        consumers: list[Consumer] | None = None,
        middleware: list[ClientCallInterceptor] | None = None,
    ):
        """Initializes the client with consumers and middleware.

        Args:
            consumers: A list of callables to process events from the agent.
            middleware: A list of interceptors to process requests and responses.
        """
        if middleware is None:
            middleware = []
        if consumers is None:
            consumers = []
        self._consumers = consumers
        self._middleware = middleware

    @abstractmethod
    async def send_message(
        self,
        request: Message,
        *,
        context: ClientCallContext | None = None,
        request_metadata: dict[str, Any] | None = None,
        extensions: list[str] | None = None,
    ) -> AsyncIterator[ClientEvent | Message]:
        """Sends a message to the server.

        This will automatically use the streaming or non-streaming approach
        as supported by the server and the client config. Client will
        aggregate update events and return an iterator of (`Task`,`Update`)
        pairs, or a `Message`. Client will also send these values to any
        configured `Consumer`s in the client.
        """
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
    ) -> AsyncIterator[ClientEvent]:
        """Resubscribes to a task's event stream."""
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
        """Retrieves the agent's card."""

    async def add_event_consumer(self, consumer: Consumer) -> None:
        """Attaches additional consumers to the `Client`."""
        self._consumers.append(consumer)

    async def add_request_middleware(
        self, middleware: ClientCallInterceptor
    ) -> None:
        """Attaches additional middleware to the `Client`."""
        self._middleware.append(middleware)

    async def consume(
        self,
        event: tuple[Task, UpdateEvent] | Message | None,
        card: AgentCard,
    ) -> None:
        """Processes the event via all the registered `Consumer`s."""
        if not event:
            return
        for c in self._consumers:
            await c(event, card)
