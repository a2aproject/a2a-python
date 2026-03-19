import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from a2a.server.routes import AgentCardRoutes
from a2a.types import a2a_pb2
from a2a.server.request_handlers.response_helpers import agent_card_to_dict


@pytest.fixture
def mock_agent_card():
    return a2a_pb2.AgentCard(
        name='test-agent',
        version='1.0.0',
        documentation_url='http://localhost:8000',
    )


@pytest.fixture
def test_app(mock_agent_card):
    app = Starlette()
    card_route = AgentCardRoutes(mock_agent_card)
    app.routes.append(card_route.route)
    return app


@pytest.fixture
def client(test_app):
    return TestClient(test_app)


def test_agent_card_route_returns_json(client, mock_agent_card):
    response = client.get('/')
    assert response.status_code == 200

    # The route returns JSON, not protobuf SerializeToString()
    actual_json = response.json()
    expected_json = agent_card_to_dict(mock_agent_card)

    assert actual_json == expected_json


def test_agent_card_route_with_modifier(mock_agent_card):
    async def modifier(card):
        card.name = 'modified-agent'
        return card

    card_route = AgentCardRoutes(mock_agent_card, card_modifier=modifier)
    app = Starlette()
    app.routes.append(card_route.routes[0])
    client = TestClient(app)

    response = client.get('/')
    assert response.status_code == 200
    assert response.json()['name'] == 'modified-agent'
