import copy

from unittest.mock import AsyncMock

import pytest

from a2a.server.routes.agent_card_routes import create_agent_card_routes
from a2a.types.a2a_pb2 import AgentCard
from a2a.utils.signing import create_agent_card_signer
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
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


def card_client(**kwargs) -> TestClient:
    return TestClient(
        Starlette(routes=create_agent_card_routes(**kwargs)),
        # Otherwise httpx resends the ETag it already saw and turns a
        # deliberate unconditional GET into a 304.
        headers={'cache-control': 'no-cache'},
    )


def test_response_carries_a_weak_etag():
    response = card_client(agent_card=AgentCard(name='a', version='1')).get(
        '/.well-known/agent-card.json'
    )

    assert response.status_code == 200
    assert response.headers['etag'].startswith('W/"')


def test_cache_control_is_sent_only_when_configured():
    url = '/.well-known/agent-card.json'
    card = AgentCard(name='a', version='1')

    assert 'cache-control' not in card_client(agent_card=card).get(url).headers
    configured = card_client(
        agent_card=card, cache_control='public, max-age=3600'
    ).get(url)
    assert configured.headers['cache-control'] == 'public, max-age=3600'


def test_etag_changes_with_the_card():
    url = '/.well-known/agent-card.json'

    first = card_client(agent_card=AgentCard(name='a', version='1')).get(url)
    second = card_client(agent_card=AgentCard(name='b', version='1')).get(url)

    assert first.headers['etag'] != second.headers['etag']


def test_etag_tracks_the_modified_card_not_the_original():
    """card_modifier can vary the body per request, so the hash must follow it."""
    url = '/.well-known/agent-card.json'

    async def rename(card: AgentCard) -> AgentCard:
        return AgentCard(name='modified', version=card.version)

    plain = card_client(agent_card=AgentCard(name='a', version='1')).get(url)
    modified = card_client(
        agent_card=AgentCard(name='a', version='1'), card_modifier=rename
    ).get(url)

    assert plain.headers['etag'] != modified.headers['etag']


@pytest.mark.parametrize(
    'make_header',
    [
        lambda etag: etag,
        # Weak comparison, so the strong spelling of the same tag matches.
        lambda etag: etag.removeprefix('W/'),
        lambda etag: '*',
        lambda etag: f'"other", {etag}',
    ],
    ids=['exact', 'strong-spelling', 'star', 'list'],
)
def test_matching_if_none_match_gets_304(make_header):
    url = '/.well-known/agent-card.json'
    client = card_client(agent_card=AgentCard(name='a', version='1'))
    etag = client.get(url).headers['etag']

    response = client.get(url, headers={'If-None-Match': make_header(etag)})

    assert response.status_code == 304
    assert response.content == b''


def test_stale_if_none_match_gets_the_card():
    url = '/.well-known/agent-card.json'
    client = card_client(agent_card=AgentCard(name='a', version='1'))

    response = client.get(url, headers={'If-None-Match': '"stale"'})

    assert response.status_code == 200
    assert response.json()['name'] == 'a'


def test_a_card_re_signed_per_request_still_revalidates():
    """The card_modifier that re-signs on every request must still get a 304.

    ES256 signatures are randomized, so an unchanged card signs differently
    every time. Hashing the signature in would produce a tag that changes on
    every request and never once matches.
    """
    url = '/.well-known/agent-card.json'
    private_key = ec.generate_private_key(ec.SECP256R1()).private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    sign = create_agent_card_signer(
        signing_key=private_key,
        protected_header={
            'alg': 'ES256',
            'kid': 'k',
            'jku': None,
            'typ': 'JOSE',
        },
    )

    async def sign_per_request(card: AgentCard) -> AgentCard:
        return sign(copy.deepcopy(card))

    client = card_client(
        agent_card=AgentCard(name='a', version='1'),
        card_modifier=sign_per_request,
    )
    first = client.get(url)
    second = client.get(url)

    # Same card, different signature, so the bodies differ byte for byte.
    assert first.json()['signatures'] != second.json()['signatures']
    assert first.headers['etag'] == second.headers['etag']
    assert (
        client.get(
            url, headers={'If-None-Match': first.headers['etag']}
        ).status_code
        == 304
    )


def test_etag_still_changes_when_a_signed_card_changes():
    """Excluding signatures must not blind the tag to real content changes."""
    url = '/.well-known/agent-card.json'
    sign = create_agent_card_signer(
        signing_key='a-secret-long-enough-for-hs256-hmac',
        protected_header={
            'alg': 'HS256',
            'kid': 'k',
            'jku': None,
            'typ': 'JOSE',
        },
    )

    def signed_client(name: str) -> TestClient:
        async def modifier(card: AgentCard) -> AgentCard:
            return sign(copy.deepcopy(card))

        return card_client(
            agent_card=AgentCard(name=name, version='1'),
            card_modifier=modifier,
        )

    first = signed_client('a').get(url)
    second = signed_client('b').get(url)

    assert first.headers['etag'] != second.headers['etag']
