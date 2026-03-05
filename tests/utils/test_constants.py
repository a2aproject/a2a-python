"""Tests for a2a.utils.constants module."""

from a2a.utils import constants


def test_agent_card_constants():
    """Test that agent card constants have expected values."""
    assert (
        constants.AGENT_CARD_WELL_KNOWN_PATH == '/.well-known/agent-card.json'
    )


def test_default_rpc_url():
    """Test default RPC URL constant."""
    assert constants.DEFAULT_RPC_URL == '/'


def test_version_header():
    """Test version header constant."""
    assert constants.VERSION_HEADER == 'A2A-Version'


def test_protocol_versions():
    """Test protocol version constants."""
    assert constants.PROTOCOL_VERSION_1_0 == '1.0'
    assert constants.PROTOCOL_VERSION_CURRENT == '1.0'
