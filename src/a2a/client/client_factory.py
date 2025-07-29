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
    from a2a.grpc import a2a_pb2_grpc
except ImportError:
    GrpcTransport = None
    a2a_pb2_grpc = None


logger = logging.getLogger(__name__)


TransportProducer = Callable[
    [AgentCard, ClientConfig, list[ClientCallInterceptor]],
    ClientTransport,
]


class ClientFactory:
    """ClientFactory is used to generate the appropriate client for the agent."""

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
        self._register_defaults()

    def _register_defaults(self) -> None:
        self.register(
            TransportProtocol.jsonrpc,
            lambda card, config, interceptors: JsonRpcTransport(
                config.httpx_client or httpx.AsyncClient(),
                card,
                card.url,
                interceptors,
            ),
        )
        self.register(
            TransportProtocol.http_json,
            lambda card, config, interceptors: RestTransport(
                config.httpx_client or httpx.AsyncClient(),
                card,
                card.url,
                interceptors,
            ),
        )
        if GrpcTransport:
            self.register(
                TransportProtocol.grpc,
                lambda card, config, interceptors: GrpcTransport(
                    a2a_pb2_grpc.A2AServiceStub(
                        config.grpc_channel_factory(card.url)
                    ),
                    card,
                ),
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
        """Create a new `Client` for the provided `AgentCard`."""
        server_set = [card.preferred_transport or TransportProtocol.jsonrpc]
        if card.additional_interfaces:
            server_set.extend([x.transport for x in card.additional_interfaces])
        client_set = self._config.supported_transports or [
            TransportProtocol.jsonrpc
        ]
        transport_protocol = None
        if self._config.use_client_preference:
            for x in client_set:
                if x in server_set:
                    transport_protocol = x
                    break
        else:
            for x in server_set:
                if x in client_set:
                    transport_protocol = x
                    break
        if not transport_protocol:
            raise ValueError('no compatible transports found.')
        if transport_protocol not in self._registry:
            raise ValueError(f'no client available for {transport_protocol}')

        all_consumers = self._consumers.copy()
        if consumers:
            all_consumers.extend(consumers)

        transport = self._registry[transport_protocol](
            card, self._config, interceptors or []
        )

        return BaseClient(
            card, self._config, transport, all_consumers, interceptors or []
        )


def minimal_agent_card(
    url: str, transports: list[str] | None = None
) -> AgentCard:
    """Generates a minimal card to simplify bootstrapping client creation."""
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
