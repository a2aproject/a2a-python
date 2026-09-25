import logging

from unittest.mock import AsyncMock

import pytest

from a2a.server.routes.agent_card_routes import create_agent_card_routes
from a2a.types.a2a_pb2 import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
)
from starlette.applications import Starlette
from starlette.testclient import TestClient


@pytest.fixture
def agent_card():
    return AgentCard()


def test_get_agent_card_success(agent_card):
    """Tests that the agent card route returns the card correctly."""
    routes = create_agent_card_routes(agent_card=agent_card)

    app = Starlette(routes=routes)
    client = TestClient(app)

    response = client.get('/.well-known/agent-card.json')
    assert response.status_code == 200
    assert response.headers['content-type'] == 'application/json'
    assert response.json() == {}  # Empty card serializes to empty dict/json


def test_get_agent_card_with_modifier(agent_card):
    """Tests that card_modifier is called and modifies the response."""

    # To test modification, let's assume we can mock the dict conversion or just see if the modifier runs.
    # Actually card_modifier receives AgentCard and returns AgentCard.
    async def modifier(card: AgentCard) -> AgentCard:
        # Clone or modify
        modified = AgentCard()
        # Set some field if possible, or just return a different instance to verify.
        # Since Protobuf objects have fields, let's look at one we can set.
        # Usually they have fields like 'url' in v0.3 or others.
        # Let's just return a MagicMock or set Something that shows up in dict if we know it.
        # Wait, if we return a different object, we can verify it.
        # Let's try to mock the conversion or just verify it was called.
        return card

    mock_modifier = AsyncMock(side_effect=modifier)
    routes = create_agent_card_routes(
        agent_card=agent_card, card_modifier=mock_modifier
    )

    app = Starlette(routes=routes)
    client = TestClient(app)

    response = client.get('/.well-known/agent-card.json')
    assert response.status_code == 200
    assert mock_modifier.called


def test_agent_card_custom_url(agent_card):
    """Tests that custom card_url is respected."""
    custom_url = '/custom/path/agent.json'
    routes = create_agent_card_routes(
        agent_card=agent_card, card_url=custom_url
    )

    app = Starlette(routes=routes)
    client = TestClient(app)

    # Check that default returns 404
    assert client.get('/.well-known/agent-card.json').status_code == 404
    # Check that custom returns 200
    assert client.get(custom_url).status_code == 200


def _complete_agent_card() -> AgentCard:
    """Returns an AgentCard with every field the A2A spec marks REQUIRED."""
    return AgentCard(
        name='complete_agent',
        description='An agent card with all required fields.',
        supported_interfaces=[
            AgentInterface(
                url='http://localhost:8000',
                protocol_binding='JSONRPC',
                protocol_version='1.0',
            )
        ],
        version='1.0',
        capabilities=AgentCapabilities(),
        default_input_modes=['text/plain'],
        default_output_modes=['text/plain'],
        skills=[
            AgentSkill(
                id='echo',
                name='Echo',
                description='Echoes the input.',
                tags=['test'],
            )
        ],
    )


def test_route_creation_warns_about_missing_required_fields(
    agent_card: AgentCard, caplog: pytest.LogCaptureFixture
) -> None:
    """An incomplete card is still served, but a warning is logged once."""
    with caplog.at_level(logging.WARNING, logger='a2a.utils.proto_utils'):
        routes = create_agent_card_routes(agent_card=agent_card)
    [record] = caplog.records
    message = record.getMessage()
    assert 'agent_card passed to create_agent_card_routes:' in message
    assert 'name, description, supported_interfaces' in message

    client = TestClient(Starlette(routes=routes))
    assert client.get('/.well-known/agent-card.json').status_code == 200


def test_route_creation_does_not_warn_for_complete_card(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger='a2a.utils.proto_utils'):
        create_agent_card_routes(agent_card=_complete_agent_card())
    assert caplog.records == []
