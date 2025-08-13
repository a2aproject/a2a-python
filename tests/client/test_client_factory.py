"""Tests for the ClientFactory."""

import httpx
import pytest

from a2a.client import ClientConfig, ClientFactory
from a2a.client.transports import JsonRpcTransport, RestTransport
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    TransportProtocol,
)


@pytest.fixture
def base_agent_card() -> AgentCard:
    """Provides a base AgentCard for tests."""
    return AgentCard(
        name='Test Agent',
        description='An agent for testing.',
        url='http://primary-url.com',
        version='1.0.0',
        capabilities=AgentCapabilities(),
        skills=[],
        default_input_modes=[],
        default_output_modes=[],
        preferred_transport=TransportProtocol.jsonrpc,
    )


def test_client_factory_selects_preferred_transport(base_agent_card: AgentCard):
    """Verify that the factory selects the preferred transport by default."""
    config = ClientConfig(
        httpx_client=httpx.AsyncClient(),
        supported_transports=[
            TransportProtocol.jsonrpc,
            TransportProtocol.http_json,
        ],
    )
    factory = ClientFactory(config)
    client = factory.create(base_agent_card)

    assert isinstance(client._transport, JsonRpcTransport)
    assert client._transport.url == 'http://primary-url.com'


def test_client_factory_selects_secondary_transport_url(
    base_agent_card: AgentCard,
):
    """Verify that the factory selects the correct URL for a secondary transport."""
    base_agent_card.additional_interfaces = [
        AgentInterface(
            transport=TransportProtocol.http_json,
            url='http://secondary-url.com',
        )
    ]
    # Client prefers REST, which is available as a secondary transport
    config = ClientConfig(
        httpx_client=httpx.AsyncClient(),
        supported_transports=[
            TransportProtocol.http_json,
            TransportProtocol.jsonrpc,
        ],
        use_client_preference=True,
    )
    factory = ClientFactory(config)
    client = factory.create(base_agent_card)

    assert isinstance(client._transport, RestTransport)
    assert client._transport.url == 'http://secondary-url.com'


def test_client_factory_server_preference(base_agent_card: AgentCard):
    """Verify that the factory respects server transport preference."""
    base_agent_card.preferred_transport = TransportProtocol.http_json
    base_agent_card.additional_interfaces = [
        AgentInterface(
            transport=TransportProtocol.jsonrpc, url='http://secondary-url.com'
        )
    ]
    # Client supports both, but server prefers REST
    config = ClientConfig(
        httpx_client=httpx.AsyncClient(),
        supported_transports=[
            TransportProtocol.jsonrpc,
            TransportProtocol.http_json,
        ],
    )
    factory = ClientFactory(config)
    client = factory.create(base_agent_card)

    assert isinstance(client._transport, RestTransport)
    assert client._transport.url == 'http://primary-url.com'


def test_client_factory_no_compatible_transport(base_agent_card: AgentCard):
    """Verify that the factory raises an error if no compatible transport is found."""
    config = ClientConfig(
        httpx_client=httpx.AsyncClient(),
        supported_transports=[TransportProtocol.grpc],
    )
    factory = ClientFactory(config)
    with pytest.raises(ValueError, match='no compatible transports found'):
        factory.create(base_agent_card)
