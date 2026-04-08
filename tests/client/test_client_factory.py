"""Tests for the ClientFactory."""

from unittest.mock import AsyncMock, MagicMock, patch
import typing

import httpx
import pytest

from a2a.client import ClientConfig, ClientFactory, create_client
from a2a.client.client_factory import TransportProducer
from a2a.client.transports import (
    JsonRpcTransport,
    RestTransport,
)
from a2a.client.transports.tenant_decorator import TenantTransportDecorator
from a2a.types.a2a_pb2 import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
)
from a2a.utils.constants import TransportProtocol


@pytest.fixture
def base_agent_card() -> AgentCard:
    """Provides a base AgentCard for tests."""
    return AgentCard(
        name='Test Agent',
        description='An agent for testing.',
        supported_interfaces=[
            AgentInterface(
                protocol_binding=TransportProtocol.JSONRPC,
                url='http://primary-url.com',
            )
        ],
        version='1.0.0',
        capabilities=AgentCapabilities(),
        skills=[],
        default_input_modes=[],
        default_output_modes=[],
    )


def test_client_factory_selects_preferred_transport(base_agent_card: AgentCard):
    """Verify that the factory selects the preferred transport by default."""
    config = ClientConfig(
        httpx_client=httpx.AsyncClient(),
        supported_protocol_bindings=[
            TransportProtocol.JSONRPC,
            TransportProtocol.HTTP_JSON,
        ],
    )
    factory = ClientFactory(config)
    client = factory.create(base_agent_card)

    assert isinstance(client._transport, JsonRpcTransport)  # type: ignore[attr-defined]
    assert client._transport.url == 'http://primary-url.com'  # type: ignore[attr-defined]


def test_client_factory_selects_secondary_transport_url(
    base_agent_card: AgentCard,
):
    """Verify that the factory selects the correct URL for a secondary transport."""
    base_agent_card.supported_interfaces.append(
        AgentInterface(
            protocol_binding=TransportProtocol.HTTP_JSON,
            url='http://secondary-url.com',
        )
    )
    # Client prefers REST, which is available as a secondary transport
    config = ClientConfig(
        httpx_client=httpx.AsyncClient(),
        supported_protocol_bindings=[
            TransportProtocol.HTTP_JSON,
            TransportProtocol.JSONRPC,
        ],
        use_client_preference=True,
    )
    factory = ClientFactory(config)
    client = factory.create(base_agent_card)

    assert isinstance(client._transport, RestTransport)  # type: ignore[attr-defined]
    assert client._transport.url == 'http://secondary-url.com'  # type: ignore[attr-defined]


def test_client_factory_server_preference(base_agent_card: AgentCard):
    """Verify that the factory respects server transport preference."""
    # Server lists REST first, which implies preference
    base_agent_card.supported_interfaces.insert(
        0,
        AgentInterface(
            protocol_binding=TransportProtocol.HTTP_JSON,
            url='http://primary-url.com',
        ),
    )
    base_agent_card.supported_interfaces.append(
        AgentInterface(
            protocol_binding=TransportProtocol.JSONRPC,
            url='http://secondary-url.com',
        )
    )
    # Client supports both, but server prefers REST
    config = ClientConfig(
        httpx_client=httpx.AsyncClient(),
        supported_protocol_bindings=[
            TransportProtocol.JSONRPC,
            TransportProtocol.HTTP_JSON,
        ],
    )
    factory = ClientFactory(config)
    client = factory.create(base_agent_card)

    assert isinstance(client._transport, RestTransport)  # type: ignore[attr-defined]
    assert client._transport.url == 'http://primary-url.com'  # type: ignore[attr-defined]


def test_client_factory_no_compatible_transport(base_agent_card: AgentCard):
    """Verify that the factory raises an error if no compatible transport is found."""
    config = ClientConfig(
        httpx_client=httpx.AsyncClient(),
        supported_protocol_bindings=['UNKNOWN_PROTOCOL'],
    )
    factory = ClientFactory(config)
    with pytest.raises(ValueError, match='no compatible transports found'):
        factory.create(base_agent_card)


def test_client_factory_create_with_default_config(
    base_agent_card: AgentCard,
):
    """Verify that create works correctly with a default ClientConfig."""
    factory = ClientFactory()
    client = factory.create(base_agent_card)
    assert isinstance(client._transport, JsonRpcTransport)  # type: ignore[attr-defined]
    assert client._transport.url == 'http://primary-url.com'  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_client_factory_create_from_url(base_agent_card: AgentCard):
    """Verify that create_from_url resolves the card and creates a client."""
    with patch('a2a.client.client_factory.A2ACardResolver') as mock_resolver:
        mock_resolver.return_value.get_agent_card = AsyncMock(
            return_value=base_agent_card
        )

        agent_url = 'http://example.com'
        factory = ClientFactory()
        client = await factory.create_from_url(agent_url)

        mock_resolver.assert_called_once()
        assert mock_resolver.call_args[0][1] == agent_url
        mock_resolver.return_value.get_agent_card.assert_awaited_once()

        assert isinstance(client._transport, JsonRpcTransport)  # type: ignore[attr-defined]
        assert client._transport.url == 'http://primary-url.com'  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_client_factory_create_from_url_uses_factory_httpx_client(
    base_agent_card: AgentCard,
):
    """Verify create_from_url uses the factory's configured httpx client."""
    with patch('a2a.client.client_factory.A2ACardResolver') as mock_resolver:
        mock_resolver.return_value.get_agent_card = AsyncMock(
            return_value=base_agent_card
        )

        agent_url = 'http://example.com'
        mock_httpx_client = httpx.AsyncClient()
        config = ClientConfig(httpx_client=mock_httpx_client)

        factory = ClientFactory(config)
        client = await factory.create_from_url(agent_url)

        mock_resolver.assert_called_once_with(mock_httpx_client, agent_url)
        mock_resolver.return_value.get_agent_card.assert_awaited_once()

        assert isinstance(client._transport, JsonRpcTransport)  # type: ignore[attr-defined]
        assert client._transport.url == 'http://primary-url.com'  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_client_factory_create_from_url_passes_resolver_args(
    base_agent_card: AgentCard,
):
    """Verify create_from_url passes resolver arguments correctly."""
    with patch('a2a.client.client_factory.A2ACardResolver') as mock_resolver:
        mock_resolver.return_value.get_agent_card = AsyncMock(
            return_value=base_agent_card
        )

        agent_url = 'http://example.com'
        relative_path = '/extendedAgentCard'
        http_kwargs = {'headers': {'X-Test': 'true'}}

        config = ClientConfig(httpx_client=httpx.AsyncClient())
        factory = ClientFactory(config)

        await factory.create_from_url(
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
async def test_client_factory_create_from_url_with_default_config(
    base_agent_card: AgentCard,
):
    """Verify create_from_url works with a default ClientConfig."""
    with patch('a2a.client.client_factory.A2ACardResolver') as mock_resolver:
        mock_resolver.return_value.get_agent_card = AsyncMock(
            return_value=base_agent_card
        )

        agent_url = 'http://example.com'
        relative_path = '/extendedAgentCard'
        http_kwargs = {'headers': {'X-Test': 'true'}}

        factory = ClientFactory()

        await factory.create_from_url(
            agent_url,
            relative_card_path=relative_path,
            resolver_http_kwargs=http_kwargs,
        )

        # Factory always creates an httpx client, so resolver gets it
        mock_resolver.assert_called_once()
        mock_resolver.return_value.get_agent_card.assert_awaited_once_with(
            relative_card_path=relative_path,
            http_kwargs=http_kwargs,
            signature_verifier=None,
        )


def test_client_factory_register_and_create_custom_transport(
    base_agent_card: AgentCard,
):
    """Verify that register() + create() uses custom transports."""

    class CustomTransport:
        pass

    def custom_transport_producer(
        *args: typing.Any, **kwargs: typing.Any
    ) -> CustomTransport:
        return CustomTransport()

    base_agent_card.supported_interfaces.insert(
        0,
        AgentInterface(protocol_binding='custom', url='custom://foo'),
    )

    config = ClientConfig(supported_protocol_bindings=['custom'])
    factory = ClientFactory(config)
    factory.register(
        'custom',
        typing.cast(TransportProducer, custom_transport_producer),
    )

    client = factory.create(base_agent_card)
    assert isinstance(client._transport, CustomTransport)  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_client_factory_create_from_url_uses_registered_transports(
    base_agent_card: AgentCard,
):
    """Verify that create_from_url() respects custom transports from register()."""

    class CustomTransport:
        pass

    def custom_transport_producer(
        *args: typing.Any, **kwargs: typing.Any
    ) -> CustomTransport:
        return CustomTransport()

    base_agent_card.supported_interfaces.insert(
        0,
        AgentInterface(protocol_binding='custom', url='custom://foo'),
    )

    with patch('a2a.client.client_factory.A2ACardResolver') as mock_resolver:
        mock_resolver.return_value.get_agent_card = AsyncMock(
            return_value=base_agent_card
        )

        config = ClientConfig(supported_protocol_bindings=['custom'])
        factory = ClientFactory(config)
        factory.register(
            'custom',
            typing.cast(TransportProducer, custom_transport_producer),
        )

        client = await factory.create_from_url('http://example.com')
        assert isinstance(client._transport, CustomTransport)  # type: ignore[attr-defined]


def test_client_factory_create_with_interceptors(
    base_agent_card: AgentCard,
):
    """Verify interceptors are passed through correctly."""
    interceptor1 = MagicMock()

    with patch('a2a.client.client_factory.BaseClient') as mock_base_client:
        factory = ClientFactory()
        factory.create(
            base_agent_card,
            interceptors=[interceptor1],
        )

        mock_base_client.assert_called_once()
        call_args = mock_base_client.call_args[0]
        assert call_args[3] == [interceptor1]


def test_client_factory_applies_tenant_decorator(base_agent_card: AgentCard):
    """Verify that the factory applies TenantTransportDecorator when tenant is present."""
    base_agent_card.supported_interfaces[0].tenant = 'my-tenant'
    config = ClientConfig(
        httpx_client=httpx.AsyncClient(),
        supported_protocol_bindings=[TransportProtocol.JSONRPC],
    )
    factory = ClientFactory(config)
    client = factory.create(base_agent_card)

    assert isinstance(client._transport, TenantTransportDecorator)  # type: ignore[attr-defined]
    assert client._transport._tenant == 'my-tenant'  # type: ignore[attr-defined]
    assert isinstance(client._transport._base, JsonRpcTransport)  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_create_client_with_agent_card(base_agent_card: AgentCard):
    """Verify create_client works when given an AgentCard directly."""
    client = await create_client(base_agent_card)
    assert isinstance(client._transport, JsonRpcTransport)  # type: ignore[attr-defined]
    assert client._transport.url == 'http://primary-url.com'  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_create_client_with_url(base_agent_card: AgentCard):
    """Verify create_client resolves a URL and creates a client."""
    with patch('a2a.client.client_factory.A2ACardResolver') as mock_resolver:
        mock_resolver.return_value.get_agent_card = AsyncMock(
            return_value=base_agent_card
        )

        client = await create_client('http://example.com')

        mock_resolver.assert_called_once()
        assert mock_resolver.call_args[0][1] == 'http://example.com'
        assert isinstance(client._transport, JsonRpcTransport)  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_create_client_with_url_and_config(base_agent_card: AgentCard):
    """Verify create_client passes client_config to the factory."""
    with patch('a2a.client.client_factory.A2ACardResolver') as mock_resolver:
        mock_resolver.return_value.get_agent_card = AsyncMock(
            return_value=base_agent_card
        )

        mock_httpx_client = httpx.AsyncClient()
        config = ClientConfig(httpx_client=mock_httpx_client)

        await create_client('http://example.com', client_config=config)

        mock_resolver.assert_called_once_with(
            mock_httpx_client, 'http://example.com'
        )
