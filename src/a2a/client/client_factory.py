from __future__ import annotations

import logging

from collections.abc import Callable

import httpx

from a2a.client.base_client import BaseClient
from a2a.client.client import Client, ClientConfig, Consumer
from a2a.client.middleware import ClientCallInterceptor
from a2a.client.transports.base import ClientTransport
from a2a.client.transports.jsonrpc import JsonRpcTransport
from a2a.client.transports.rest import RestTransport
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    TransportProtocol,
)


try:
    from a2a.client.transports.grpc import GrpcTransport
except ImportError:
    GrpcTransport = None  # type: ignore # pyright: ignore


logger = logging.getLogger(__name__)


TransportProducer = Callable[
    [AgentCard, str, ClientConfig, list[ClientCallInterceptor]],
    ClientTransport,
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
        self._registry: dict[str, TransportProducer] = {}
        self._register_defaults(config.supported_transports)

    def _register_defaults(
        self, supported: list[str | TransportProtocol]
    ) -> None:
        # Empty support list implies JSON-RPC only.
        if TransportProtocol.jsonrpc in supported or not supported:
            self.register(
                TransportProtocol.jsonrpc,
                lambda card, url, config, interceptors: JsonRpcTransport(
                    config.httpx_client or httpx.AsyncClient(),
                    card,
                    url,
                    interceptors,
                ),
            )
        if TransportProtocol.http_json in supported:
            self.register(
                TransportProtocol.http_json,
                lambda card, url, config, interceptors: RestTransport(
                    config.httpx_client or httpx.AsyncClient(),
                    card,
                    url,
                    interceptors,
                ),
            )
        if TransportProtocol.grpc in supported:
            if GrpcTransport is None:
                raise ImportError(
                    'To use GrpcClient, its dependencies must be installed. '
                    'You can install them with \'pip install "a2a-sdk[grpc]"\''
                )
            self.register(
                TransportProtocol.grpc,
                GrpcTransport.create,
            )

    def register(self, label: str, generator: TransportProducer) -> None:
        """Register a new transport producer for a given transport label."""
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
        server_preferred = card.preferred_transport or TransportProtocol.jsonrpc
        server_set = {server_preferred: card.url}
        if card.additional_interfaces:
            server_set.update(
                {x.transport: x.url for x in card.additional_interfaces}
            )
        client_set = self._config.supported_transports or [
            TransportProtocol.jsonrpc
        ]
        transport_protocol = None
        transport_url = None
        if self._config.use_client_preference:
            for x in client_set:
                if x in server_set:
                    transport_protocol = x
                    transport_url = server_set[x]
                    break
        else:
            for x, url in server_set.items():
                if x in client_set:
                    transport_protocol = x
                    transport_url = url
                    break
        if not transport_protocol or not transport_url:
            raise ValueError('no compatible transports found.')
        if transport_protocol not in self._registry:
            raise ValueError(f'no client available for {transport_protocol}')

        all_consumers = self._consumers.copy()
        if consumers:
            all_consumers.extend(consumers)

        transport = self._registry[transport_protocol](
            card, transport_url, self._config, interceptors or []
        )

        return BaseClient(
            card, self._config, transport, all_consumers, interceptors or []
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
