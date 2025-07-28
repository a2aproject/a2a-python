from __future__ import annotations

import logging

from collections.abc import Callable

from a2a.client.client import Client, ClientConfig, Consumer
from a2a.client.grpc_client import NewGrpcClient
from a2a.client.jsonrpc_client import NewJsonRpcClient
from a2a.client.middleware import ClientCallInterceptor
from a2a.client.rest_client import NewRestfulClient
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    TransportProtocol,
)


logger = logging.getLogger(__name__)

ClientProducer = Callable[
    [
        AgentCard,
        ClientConfig,
        list[Consumer],
        list[ClientCallInterceptor],
    ],
    Client,
]


class ClientFactory:
    """ClientFactory is used to generate the appropriate client for the agent.

    The factory is configured with a `ClientConfig` and optionally a list of
    `Consumer`s to use for all generated `Client`s. The expected use is:

    factory = ClientFactory(config, consumers)
    # Optionally register custom client implementations
    factory.register('my_customer_transport', NewCustomTransportClient)
    # Then with an agent card make a client with additional consumers and
    # interceptors
    client = factory.create(card, additional_consumers, interceptors)
    # Now the client can be used the same regardless of transport and
    # aligns client config with server capabilities.
    """

    def __init__(
        self,
        config: ClientConfig,
        consumers: list[Consumer] | None = None,
    ):
        if consumers is None:
            consumers = []
        self._config = config
        self._consumers = consumers
        self._registry: dict[str, ClientProducer] = {}
        # By default register the 3 core transports if in the config.
        # Can be overridden with custom clients via the register method.
        if TransportProtocol.jsonrpc in self._config.supported_transports:
            self._registry[TransportProtocol.jsonrpc] = NewJsonRpcClient
        if TransportProtocol.http_json in self._config.supported_transports:
            self._registry[TransportProtocol.http_json] = NewRestfulClient
        if TransportProtocol.grpc in self._config.supported_transports:
            self._registry[TransportProtocol.grpc] = NewGrpcClient

    def register(self, label: str, generator: ClientProducer) -> None:
        """Register a new client producer for a given transport label."""
        self._registry[label] = generator

    def create(
        self,
        card: AgentCard,
        consumers: list[Consumer] | None = None,
        interceptors: list[ClientCallInterceptor] | None = None,
    ) -> Client:
        """Create a new `Client` for the provided `AgentCard`.

        Args:
          card: An `AgentCard` defining the characteristics of the agent.
          consumers: A list of `Consumer` methods to pass responses to.
          interceptors: A list of interceptors to use for each request. These
            are used for things like attaching credentials or http headers
            to all outbound requests.

        Returns:
          A `Client` object.

        Raises:
          If there is no valid matching of the client configuration with the
          server configuration, a `ValueError` is raised.
        """
        # Determine preferential transport
        server_set = [card.preferred_transport or TransportProtocol.jsonrpc]
        if card.additional_interfaces:
            server_set.extend([x.transport for x in card.additional_interfaces])
        client_set = self._config.supported_transports or [
            TransportProtocol.jsonrpc
        ]
        transport = None
        # Two options, use the client ordering or the server ordering.
        if self._config.use_client_preference:
            for x in client_set:
                if x in server_set:
                    transport = x
                    break
        else:
            for x in server_set:
                if x in client_set:
                    transport = x
                    break
        if not transport:
            raise ValueError('no compatible transports found.')
        if transport not in self._registry:
            raise ValueError(f'no client available for {transport}')
        all_consumers = self._consumers.copy()
        if consumers:
            all_consumers.extend(consumers)
        return self._registry[transport](
            card, self._config, all_consumers, interceptors
        )


def minimal_agent_card(
    url: str, transports: list[str] | None = None
) -> AgentCard:
    """Generates a minimal card to simplify bootstrapping client creation.

    This minimal card is not viable itself to interact with the remote agent.
    Instead this is a short hand way to take a known url and transport option
    and interact with the get card endpoint of the agent server to get the
    correct agent card. This pattern is necessary for gRPC based card access
    as typically these servers won't expose a well known path card.
    """
    if transports is None:
        transports = []
    return AgentCard(
        url=url,
        preferred_transport=transports[0] if transports else None,
        additional_interfaces=[
            AgentInterface(transport=t, url=url) for t in transports[1:]
        ]
        if len(transports) > 1
        else [],
        supports_authenticated_extended_card=True,
        capabilities=AgentCapabilities(),
        default_input_modes=[],
        default_output_modes=[],
        description='',
        skills=[],
        version='',
        name='',
    )
