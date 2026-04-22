"""Tests for GRPC transport selection in ClientFactory."""

from unittest.mock import MagicMock, patch
import pytest

from a2a.client import ClientConfig, ClientFactory
from a2a.types.a2a_pb2 import AgentCard, AgentInterface, AgentCapabilities
from a2a.utils.constants import TransportProtocol


@pytest.fixture
def grpc_agent_card() -> AgentCard:
    """Provides an AgentCard with GRPC interfaces for tests."""
    return AgentCard(
        supported_interfaces=[],
        capabilities=AgentCapabilities(),
        skills=[],
        default_input_modes=[],
        default_output_modes=[],
        name='GRPC Agent',
        version='1.0.0',
        description='Test agent',
    )


def test_grpc_priority_1_0(grpc_agent_card):
    """Verify that protocol version 1.0 has the highest priority and uses GrpcTransport."""
    grpc_agent_card.supported_interfaces.extend(
        [
            AgentInterface(
                protocol_binding=TransportProtocol.GRPC,
                url='url03',
                protocol_version='0.3',
            ),
            AgentInterface(
                protocol_binding=TransportProtocol.GRPC,
                url='url11',
                protocol_version='1.1',
            ),
            AgentInterface(
                protocol_binding=TransportProtocol.GRPC,
                url='url10',
                protocol_version='1.0',
            ),
        ]
    )

    config = ClientConfig(
        supported_protocol_bindings=[TransportProtocol.GRPC],
        grpc_channel_factory=MagicMock(),
    )

    # We patch GrpcTransport and CompatGrpcTransport in the client_factory module
    with (
        patch('a2a.client.client_factory.GrpcTransport') as mock_grpc,
        patch('a2a.client.client_factory.CompatGrpcTransport') as mock_compat,
    ):
        factory = ClientFactory(config)
        factory.create(grpc_agent_card)

        # Priority 1: 1.0 -> GrpcTransport
        mock_grpc.create.assert_called_once_with(
            grpc_agent_card, 'url10', config
        )
        mock_compat.create.assert_not_called()


def test_grpc_priority_gt_1_0(grpc_agent_card):
    """Verify that protocol version > 1.0 uses GrpcTransport (first one found)."""
    grpc_agent_card.supported_interfaces.extend(
        [
            AgentInterface(
                protocol_binding=TransportProtocol.GRPC,
                url='url03',
                protocol_version='0.3',
            ),
            AgentInterface(
                protocol_binding=TransportProtocol.GRPC,
                url='url11',
                protocol_version='1.1',
            ),
            AgentInterface(
                protocol_binding=TransportProtocol.GRPC,
                url='url12',
                protocol_version='1.2',
            ),
        ]
    )

    config = ClientConfig(
        supported_protocol_bindings=[TransportProtocol.GRPC],
        grpc_channel_factory=MagicMock(),
    )

    with (
        patch('a2a.client.client_factory.GrpcTransport') as mock_grpc,
        patch('a2a.client.client_factory.CompatGrpcTransport') as mock_compat,
    ):
        factory = ClientFactory(config)
        factory.create(grpc_agent_card)

        # Priority 2: > 1.0 -> GrpcTransport (first matching is 1.1)
        mock_grpc.create.assert_called_once_with(
            grpc_agent_card, 'url11', config
        )
        mock_compat.create.assert_not_called()


def test_grpc_priority_lt_0_3_raises_value_error(grpc_agent_card):
    """Verify that if the only available interface has version < 0.3, it raises a ValueError."""
    grpc_agent_card.supported_interfaces.extend(
        [
            AgentInterface(
                protocol_binding=TransportProtocol.GRPC,
                url='url02',
                protocol_version='0.2',
            ),
        ]
    )

    config = ClientConfig(
        supported_protocol_bindings=[TransportProtocol.GRPC],
        grpc_channel_factory=MagicMock(),
    )

    factory = ClientFactory(config)
    with pytest.raises(ValueError, match='no compatible transports found'):
        factory.create(grpc_agent_card)


def test_grpc_invalid_version_raises_value_error(grpc_agent_card):
    """Verify that if only an invalid version is available, it raises a ValueError (it's ignored)."""
    grpc_agent_card.supported_interfaces.extend(
        [
            AgentInterface(
                protocol_binding=TransportProtocol.GRPC,
                url='url_invalid',
                protocol_version='invalid_version_string',
            ),
        ]
    )

    config = ClientConfig(
        supported_protocol_bindings=[TransportProtocol.GRPC],
        grpc_channel_factory=MagicMock(),
    )

    factory = ClientFactory(config)
    with pytest.raises(ValueError, match='no compatible transports found'):
        factory.create(grpc_agent_card)


def test_grpc_unspecified_version_uses_grpc_transport(grpc_agent_card):
    """Verify that if no version is specified, it defaults to GrpcTransport."""
    grpc_agent_card.supported_interfaces.extend(
        [
            AgentInterface(
                protocol_binding=TransportProtocol.GRPC,
                url='url_no_version',
            ),
        ]
    )

    config = ClientConfig(
        supported_protocol_bindings=[TransportProtocol.GRPC],
        grpc_channel_factory=MagicMock(),
    )

    with patch('a2a.client.client_factory.GrpcTransport') as mock_grpc:
        factory = ClientFactory(config)
        factory.create(grpc_agent_card)

        mock_grpc.create.assert_called_once_with(
            grpc_agent_card, 'url_no_version', config
        )
