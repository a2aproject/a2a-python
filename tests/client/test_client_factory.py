"""Tests for the ClientFactory."""

from unittest.mock import AsyncMock, MagicMock, patch

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
        extensions=['https://example.com/test-ext/v0'],
    )
    factory = ClientFactory(config)
    client = factory.create(base_agent_card)

    assert isinstance(client._transport, JsonRpcTransport)
    assert client._transport.url == 'http://primary-url.com'
    assert ['https://example.com/test-ext/v0'] == client._transport.extensions


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
        extensions=['https://example.com/test-ext/v0'],
    )
    factory = ClientFactory(config)
    client = factory.create(base_agent_card)

    assert isinstance(client._transport, RestTransport)
    assert client._transport.url == 'http://secondary-url.com'
    assert ['https://example.com/test-ext/v0'] == client._transport.extensions


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


@pytest.mark.asyncio
async def test_client_factory_connect_with_agent_card(
    base_agent_card: AgentCard,
):
    """Verify that connect works correctly when provided with an AgentCard."""
    client = await ClientFactory.connect(base_agent_card)
    assert isinstance(client._transport, JsonRpcTransport)
    assert client._transport.url == 'http://primary-url.com'


@pytest.mark.asyncio
async def test_client_factory_connect_with_url(base_agent_card: AgentCard):
    """Verify that connect works correctly when provided with a URL."""
    with patch('a2a.client.client_factory.A2ACardResolver') as mock_resolver:
        mock_resolver.return_value.get_agent_card = AsyncMock(
            return_value=base_agent_card
        )

        agent_url = 'http://example.com'
        client = await ClientFactory.connect(agent_url)

        mock_resolver.assert_called_once()
        assert mock_resolver.call_args[0][1] == agent_url
        mock_resolver.return_value.get_agent_card.assert_awaited_once()

        assert isinstance(client._transport, JsonRpcTransport)
        assert client._transport.url == 'http://primary-url.com'


@pytest.mark.asyncio
async def test_client_factory_connect_with_url_and_client_config(
    base_agent_card: AgentCard,
):
    """Verify connect with a URL and a pre-configured httpx client."""
    with patch('a2a.client.client_factory.A2ACardResolver') as mock_resolver:
        mock_resolver.return_value.get_agent_card = AsyncMock(
            return_value=base_agent_card
        )

        agent_url = 'http://example.com'
        mock_httpx_client = httpx.AsyncClient()
        config = ClientConfig(httpx_client=mock_httpx_client)

        client = await ClientFactory.connect(agent_url, client_config=config)

        mock_resolver.assert_called_once_with(mock_httpx_client, agent_url)
        mock_resolver.return_value.get_agent_card.assert_awaited_once()

        assert isinstance(client._transport, JsonRpcTransport)
        assert client._transport.url == 'http://primary-url.com'


@pytest.mark.asyncio
async def test_client_factory_connect_with_resolver_args(
    base_agent_card: AgentCard,
):
    """Verify connect passes resolver arguments correctly."""
    with patch('a2a.client.client_factory.A2ACardResolver') as mock_resolver:
        mock_resolver.return_value.get_agent_card = AsyncMock(
            return_value=base_agent_card
        )

        agent_url = 'http://example.com'
        relative_path = '/card'
        http_kwargs = {'headers': {'X-Test': 'true'}}

        # The resolver args are only passed if an httpx_client is provided in config
        config = ClientConfig(httpx_client=httpx.AsyncClient())

        await ClientFactory.connect(
            agent_url,
            client_config=config,
            relative_card_path=relative_path,
            resolver_http_kwargs=http_kwargs,
        )

        mock_resolver.return_value.get_agent_card.assert_awaited_once_with(
            relative_card_path=relative_path,
            http_kwargs=http_kwargs,
            signature_verifier=None,
        )


@pytest.mark.asyncio
async def test_client_factory_connect_resolver_args_without_client(
    base_agent_card: AgentCard,
):
    """Verify resolver args are ignored if no httpx_client is provided."""
    with patch('a2a.client.client_factory.A2ACardResolver') as mock_resolver:
        mock_resolver.return_value.get_agent_card = AsyncMock(
            return_value=base_agent_card
        )

        agent_url = 'http://example.com'
        relative_path = '/card'
        http_kwargs = {'headers': {'X-Test': 'true'}}

        await ClientFactory.connect(
            agent_url,
            relative_card_path=relative_path,
            resolver_http_kwargs=http_kwargs,
        )

        mock_resolver.return_value.get_agent_card.assert_awaited_once_with(
            relative_card_path=relative_path,
            http_kwargs=http_kwargs,
            signature_verifier=None,
        )


@pytest.mark.asyncio
async def test_client_factory_connect_with_extra_transports(
    base_agent_card: AgentCard,
):
    """Verify that connect can register and use extra transports."""

    class CustomTransport:
        pass

    def custom_transport_producer(*args, **kwargs):
        return CustomTransport()

    base_agent_card.preferred_transport = 'custom'
    base_agent_card.url = 'custom://foo'

    config = ClientConfig(supported_transports=['custom'])

    client = await ClientFactory.connect(
        base_agent_card,
        client_config=config,
        extra_transports={'custom': custom_transport_producer},
    )

    assert isinstance(client._transport, CustomTransport)


@pytest.mark.asyncio
async def test_client_factory_connect_with_consumers_and_interceptors(
    base_agent_card: AgentCard,
):
    """Verify consumers and interceptors are passed through correctly."""
    consumer1 = MagicMock()
    interceptor1 = MagicMock()

    with patch('a2a.client.client_factory.BaseClient') as mock_base_client:
        await ClientFactory.connect(
            base_agent_card,
            consumers=[consumer1],
            interceptors=[interceptor1],
        )

        mock_base_client.assert_called_once()
        call_args = mock_base_client.call_args[0]
        assert call_args[3] == [consumer1]
        assert call_args[4] == [interceptor1]
