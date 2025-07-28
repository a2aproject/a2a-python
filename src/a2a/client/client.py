import dataclasses
import json
import logging

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Callable, Coroutine
from typing import TYPE_CHECKING, Any

import httpx

from pydantic import ValidationError


# Attempt to import the optional module
try:
    from grpc.aio import Channel
except ImportError:
    # If grpc.aio is not available, define a dummy type for type checking.
    # This dummy type will only be used by type checkers.
    if TYPE_CHECKING:

        class Channel:  # type: ignore[no-redef]
            """Dummy class for type hinting when grpc.aio is not available."""

    else:
        Channel = None  # At runtime, pd will be None if the import failed.

from a2a.client.errors import (
    A2AClientHTTPError,
    A2AClientJSONError,
)
from a2a.client.middleware import ClientCallContext, ClientCallInterceptor
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
from a2a.utils.constants import AGENT_CARD_WELL_KNOWN_PATH


logger = logging.getLogger(__name__)


class A2ACardResolver:
    """Agent Card resolver."""

    def __init__(
        self,
        httpx_client: httpx.AsyncClient,
        base_url: str,
        agent_card_path: str = AGENT_CARD_WELL_KNOWN_PATH,
    ) -> None:
        """Initializes the A2ACardResolver.

        Args:
            httpx_client: An async HTTP client instance (e.g., httpx.AsyncClient).
            base_url: The base URL of the agent's host.
            agent_card_path: The path to the agent card endpoint, relative to the base URL.
        """
        self.base_url = base_url.rstrip('/')
        self.agent_card_path = agent_card_path.lstrip('/')
        self.httpx_client = httpx_client

    async def get_agent_card(
        self,
        relative_card_path: str | None = None,
        http_kwargs: dict[str, Any] | None = None,
    ) -> AgentCard:
        """Fetches an agent card from a specified path relative to the base_url.

        If relative_card_path is None, it defaults to the resolver's configured
        agent_card_path (for the public agent card).

        Args:
            relative_card_path: Optional path to the agent card endpoint,
                relative to the base URL. If None, uses the default public
                agent card path.
            http_kwargs: Optional dictionary of keyword arguments to pass to the
                underlying httpx.get request.

        Returns:
            An `AgentCard` object representing the agent's capabilities.

        Raises:
            A2AClientHTTPError: If an HTTP error occurs during the request.
            A2AClientJSONError: If the response body cannot be decoded as JSON
                or validated against the AgentCard schema.
        """
        if relative_card_path is None:
            # Use the default public agent card path configured during initialization
            path_segment = self.agent_card_path
        else:
            path_segment = relative_card_path.lstrip('/')

        target_url = f'{self.base_url}/{path_segment}'

        try:
            response = await self.httpx_client.get(
                target_url,
                **(http_kwargs or {}),
            )
            response.raise_for_status()
            agent_card_data = response.json()
            logger.info(
                'Successfully fetched agent card data from %s: %s',
                target_url,
                agent_card_data,
            )
            agent_card = AgentCard.model_validate(agent_card_data)
        except httpx.HTTPStatusError as e:
            raise A2AClientHTTPError(
                e.response.status_code,
                f'Failed to fetch agent card from {target_url}: {e}',
            ) from e
        except json.JSONDecodeError as e:
            raise A2AClientJSONError(
                f'Failed to parse JSON for agent card from {target_url}: {e}'
            ) from e
        except httpx.RequestError as e:
            raise A2AClientHTTPError(
                503,
                f'Network communication error fetching agent card from {target_url}: {e}',
            ) from e
        except ValidationError as e:  # Pydantic validation error
            raise A2AClientJSONError(
                f'Failed to validate agent card structure from {target_url}: {e.json()}'
            ) from e

        return agent_card


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
    ) -> AsyncIterator[ClientEvent | Message]:
        """Sends a message to the server.

        This will automatically use the streaming or non-streaming approach
        as supported by the server and the client config. Client will
        aggregate update events and return an iterator of (`Task`,`Update`)
        pairs, or a `Message`. Client will also send these values to any
        configured `Consumer`s in the client.
        """

    @abstractmethod
    async def get_task(
        self,
        request: TaskQueryParams,
        *,
        context: ClientCallContext | None = None,
    ) -> Task:
        """Retrieves the current state and history of a specific task."""

    @abstractmethod
    async def cancel_task(
        self,
        request: TaskIdParams,
        *,
        context: ClientCallContext | None = None,
    ) -> Task:
        """Requests the agent to cancel a specific task."""

    @abstractmethod
    async def set_task_callback(
        self,
        request: TaskPushNotificationConfig,
        *,
        context: ClientCallContext | None = None,
    ) -> TaskPushNotificationConfig:
        """Sets or updates the push notification configuration for a specific task."""

    @abstractmethod
    async def get_task_callback(
        self,
        request: GetTaskPushNotificationConfigParams,
        *,
        context: ClientCallContext | None = None,
    ) -> TaskPushNotificationConfig:
        """Retrieves the push notification configuration for a specific task."""

    @abstractmethod
    async def resubscribe(
        self,
        request: TaskIdParams,
        *,
        context: ClientCallContext | None = None,
    ) -> AsyncIterator[Task | Message]:
        """Resubscribes to a task's event stream."""

    @abstractmethod
    async def get_card(
        self, *, context: ClientCallContext | None = None
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
