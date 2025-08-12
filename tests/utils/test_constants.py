"""Tests for a2a.utils.constants module."""

from a2a.utils import constants


def test_agent_card_constants():
    """Test that agent card constants have expected values."""
    assert (
        constants.AGENT_CARD_WELL_KNOWN_PATH == '/.well-known/agent-card.json'
    )
    assert (
        constants.PREV_AGENT_CARD_WELL_KNOWN_PATH == '/.well-known/agent.json'
    )
    assert (
        constants.EXTENDED_AGENT_CARD_PATH == '/agent/authenticatedExtendedCard'
    )


def test_default_rpc_url():
    """Test default RPC URL constant."""
    assert constants.DEFAULT_RPC_URL == '/'
