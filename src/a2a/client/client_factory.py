from __future__ import annotations

import logging

from collections.abc import Callable
from typing import Any

import httpx

from a2a.client.base_client import BaseClient
from a2a.client.card_resolver import A2ACardResolver
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

    .. code-block:: python

        factory = ClientFactory(config, consumers)
        # Optionally register custom client implementations
        factory.register('my_customer_transport', NewCustomTransportClient)
        # Then with an agent card make a client with additional consumers and
        # interceptors
        client = factory.create(card, additional_consumers, interceptors)

    Now the client can be used consistently regardless of the transport. This
    aligns the client configuration with the server's capabilities.
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
                    config.extensions or None,
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
                    config.extensions or None,
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

    @classmethod
    async def connect(  # noqa: PLR0913
        cls,
        agent: str | AgentCard,
        client_config: ClientConfig | None = None,
        consumers: list[Consumer] | None = None,
        interceptors: list[ClientCallInterceptor] | None = None,
        relative_card_path: str | None = None,
        resolver_http_kwargs: dict[str, Any] | None = None,
        extra_transports: dict[str, TransportProducer] | None = None,
        extensions: list[str] | None = None,
        signature_verifier: Callable[[AgentCard], None] | None = None,
    ) -> Client:
        """Convenience method for constructing a client.

        Constructs a client that connects to the specified agent. Note that
        creating multiple clients via this method is less efficient than
        constructing an instance of ClientFactory and reusing that.

        .. code-block:: python

            # This will search for an AgentCard at /.well-known/agent-card.json
            my_agent_url = 'https://travel.agents.example.com'
            client = await ClientFactory.connect(my_agent_url)


        Args:
          agent: The base URL of the agent, or the AgentCard to connect to.
          client_config: The ClientConfig to use when connecting to the agent.
          consumers: A list of `Consumer` methods to pass responses to.
          interceptors: A list of interceptors to use for each request. These
            are used for things like attaching credentials or http headers
            to all outbound requests.
          relative_card_path: If the agent field is a URL, this value is used as
            the relative path when resolving the agent card. See
            A2AAgentCardResolver.get_agent_card for more details.
          resolver_http_kwargs: Dictionary of arguments to provide to the httpx
            client when resolving the agent card. This value is provided to
            A2AAgentCardResolver.get_agent_card as the http_kwargs parameter.
          extra_transports: Additional transport protocols to enable when
            constructing the client.
          extensions: List of extensions to be activated.
          signature_verifier: A callable used to verify the agent card's signatures.

        Returns:
          A `Client` object.
        """
        client_config = client_config or ClientConfig()
        if isinstance(agent, str):
            if not client_config.httpx_client:
                async with httpx.AsyncClient() as client:
                    resolver = A2ACardResolver(client, agent)
                    card = await resolver.get_agent_card(
                        relative_card_path=relative_card_path,
                        http_kwargs=resolver_http_kwargs,
                        signature_verifier=signature_verifier,
                    )
            else:
                resolver = A2ACardResolver(client_config.httpx_client, agent)
                card = await resolver.get_agent_card(
                    relative_card_path=relative_card_path,
                    http_kwargs=resolver_http_kwargs,
                    signature_verifier=signature_verifier,
                )
        else:
            card = agent
        factory = cls(client_config)
        for label, generator in (extra_transports or {}).items():
            factory.register(label, generator)
        return factory.create(card, consumers, interceptors, extensions)

    def register(self, label: str, generator: TransportProducer) -> None:
        """Register a new transport producer for a given transport label."""
        self._registry[label] = generator

    def create(
        self,
        card: AgentCard,
        consumers: list[Consumer] | None = None,
        interceptors: list[ClientCallInterceptor] | None = None,
        extensions: list[str] | None = None,
    ) -> Client:
        """Create a new `Client` for the provided `AgentCard`.

        Args:
          card: An `AgentCard` defining the characteristics of the agent.
          consumers: A list of `Consumer` methods to pass responses to.
          interceptors: A list of interceptors to use for each request. These
            are used for things like attaching credentials or http headers
            to all outbound requests.
          extensions: List of extensions to be activated.

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

        all_extensions = self._config.extensions.copy()
        if extensions:
            all_extensions.extend(extensions)
            self._config.extensions = all_extensions

        transport = self._registry[transport_protocol](
            card, transport_url, self._config, interceptors or []
        )

        return BaseClient(
            card,
            self._config,
            transport,
            all_consumers,
            interceptors or [],
        )


def minimal_agent_card(
    url: str, transports: list[str] | None = None
) -> AgentCard:
    """Generates a minimal card to simplify bootstrapping client creation.

    This minimal card is not viable itself to interact with the remote agent.
    Instead this is a shorthand way to take a known url and transport option
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
