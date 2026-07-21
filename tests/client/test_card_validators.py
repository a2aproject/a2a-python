"""Tests for optional AgentCard validation hooks."""

import httpx
import pytest

from a2a.client import (
    ClientConfig,
    ClientFactory,
    InvalidAgentCardError,
    reject_non_https_urls,
    reject_private_urls,
    require_supported_interfaces,
)
from a2a.types.a2a_pb2 import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
)
from a2a.utils.constants import TransportProtocol


def _valid_card(**overrides) -> AgentCard:
    card = AgentCard(
        name='Test Agent',
        description='An agent for testing.',
        supported_interfaces=[
            AgentInterface(
                protocol_binding=TransportProtocol.JSONRPC,
                url='https://primary-url.com',
            )
        ],
        version='1.0.0',
        capabilities=AgentCapabilities(),
        skills=[],
        default_input_modes=[],
        default_output_modes=[],
    )
    for key, value in overrides.items():
        if key == 'supported_interfaces':
            card.ClearField('supported_interfaces')
            card.supported_interfaces.extend(value)
        else:
            setattr(card, key, value)
    return card


def test_require_supported_interfaces_rejects_empty_card():
    card = _valid_card()
    card.ClearField('supported_interfaces')

    with pytest.raises(InvalidAgentCardError, match='supported interface'):
        require_supported_interfaces(card)


def test_reject_non_https_urls_rejects_http_interface():
    card = _valid_card(
        supported_interfaces=[
            AgentInterface(
                protocol_binding=TransportProtocol.JSONRPC,
                url='http://insecure.example.com',
            )
        ]
    )

    with pytest.raises(InvalidAgentCardError, match='https'):
        reject_non_https_urls(card)


def test_reject_private_urls_rejects_loopback():
    card = _valid_card(
        supported_interfaces=[
            AgentInterface(
                protocol_binding=TransportProtocol.JSONRPC,
                url='https://127.0.0.1',
            )
        ]
    )

    with pytest.raises(InvalidAgentCardError, match='private or loopback'):
        reject_private_urls(card)


def test_client_factory_runs_validators_on_create():
    card = _valid_card(
        supported_interfaces=[
            AgentInterface(
                protocol_binding=TransportProtocol.JSONRPC,
                url='http://insecure.example.com',
            )
        ]
    )
    factory = ClientFactory(
        ClientConfig(httpx_client=httpx.AsyncClient()),
        card_validators=[reject_non_https_urls],
    )

    with pytest.raises(InvalidAgentCardError):
        factory.create(card)


def test_client_factory_default_has_no_validation():
    card = _valid_card()
    factory = ClientFactory(ClientConfig(httpx_client=httpx.AsyncClient()))
    client = factory.create(card)
    assert client is not None
