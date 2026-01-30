from a2a.types import (
    AgentCard,
    AgentCapabilities,
    AgentSkill,
)
from a2a.types import (
    AgentCard,
    AgentCapabilities,
    AgentSkill,
    AgentCardSignature,
)
from a2a.utils import signing
from typing import Any
from jwt.utils import base64url_encode

import pytest
from cryptography.hazmat.primitives import asymmetric


def create_key_provider(verification_key: str | bytes | dict[str, Any]):
    """Creates a key provider function for testing."""

    def key_provider(kid: str | None, jku: str | None):
        return verification_key

    return key_provider


# Fixture for a complete sample AgentCard
@pytest.fixture
def sample_agent_card() -> AgentCard:
    return AgentCard(
        name='Test Agent',
        description='A test agent',
        url='http://localhost',
        version='1.0.0',
        capabilities=AgentCapabilities(
            streaming=None,
            push_notifications=True,
        ),
        default_input_modes=['text/plain'],
        default_output_modes=['text/plain'],
        documentation_url=None,
        icon_url='',
        skills=[
            AgentSkill(
                id='skill1',
                name='Test Skill',
                description='A test skill',
                tags=['test'],
            )
        ],
    )


def test_signer_and_verifier_symmetric(sample_agent_card: AgentCard):
    """Test the agent card signing and verification process with symmetric key encryption."""
    key = 'key12345'  # Using a simple symmetric key for HS256
    wrong_key = 'wrongkey'

    agent_card_signer = signing.create_agent_card_signer(
        signing_key=key,
        protected_header={
            'alg': 'HS384',
            'kid': 'key1',
            'jku': None,
            'typ': 'JOSE',
        },
    )
    signed_card = agent_card_signer(sample_agent_card)

    assert signed_card.signatures is not None
    assert len(signed_card.signatures) == 1
    signature = signed_card.signatures[0]
    assert signature.protected is not None
    assert signature.signature is not None

    # Verify the signature
    verifier = signing.create_signature_verifier(
        create_key_provider(key), ['HS256', 'HS384', 'ES256', 'RS256']
    )
    try:
        verifier(signed_card)
    except signing.InvalidSignaturesError:
        pytest.fail('Signature verification failed with correct key')

    # Verify with wrong key
    verifier_wrong_key = signing.create_signature_verifier(
        create_key_provider(wrong_key), ['HS256', 'HS384', 'ES256', 'RS256']
    )
    with pytest.raises(signing.InvalidSignaturesError):
        verifier_wrong_key(signed_card)


def test_signer_and_verifier_symmetric_multiple_signatures(
    sample_agent_card: AgentCard,
):
    """Test the agent card signing and verification process with symmetric key encryption.
    This test adds a signatures to the AgentCard before signing."""
    encoded_header = base64url_encode(
        b'{"alg": "HS256", "kid": "old_key"}'
    ).decode('utf-8')
    sample_agent_card.signatures = [
        AgentCardSignature(protected=encoded_header, signature='old_signature')
    ]
    key = 'key12345'  # Using a simple symmetric key for HS256
    wrong_key = 'wrongkey'

    agent_card_signer = signing.create_agent_card_signer(
        signing_key=key,
        protected_header={
            'alg': 'HS384',
            'kid': 'key1',
            'jku': None,
            'typ': 'JOSE',
        },
    )
    signed_card = agent_card_signer(sample_agent_card)

    assert signed_card.signatures is not None
    assert len(signed_card.signatures) == 2
    signature = signed_card.signatures[1]
    assert signature.protected is not None
    assert signature.signature is not None

    # Verify the signature
    verifier = signing.create_signature_verifier(
        create_key_provider(key), ['HS256', 'HS384', 'ES256', 'RS256']
    )
    try:
        verifier(signed_card)
    except signing.InvalidSignaturesError:
        pytest.fail('Signature verification failed with correct key')

    # Verify with wrong key
    verifier_wrong_key = signing.create_signature_verifier(
        create_key_provider(wrong_key), ['HS256', 'HS384', 'ES256', 'RS256']
    )
    with pytest.raises(signing.InvalidSignaturesError):
        verifier_wrong_key(signed_card)


def test_signer_and_verifier_asymmetric(sample_agent_card: AgentCard):
    """Test the agent card signing and verification process with an asymmetric key encryption."""
    # Generate a dummy EC private key for ES256
    private_key = asymmetric.ec.generate_private_key(asymmetric.ec.SECP256R1())
    public_key = private_key.public_key()
    # Generate another key pair for negative test
    private_key_error = asymmetric.ec.generate_private_key(
        asymmetric.ec.SECP256R1()
    )
    public_key_error = private_key_error.public_key()

    agent_card_signer = signing.create_agent_card_signer(
        signing_key=private_key,
        protected_header={
            'alg': 'ES256',
            'kid': 'key2',
            'jku': None,
            'typ': 'JOSE',
        },
    )
    signed_card = agent_card_signer(sample_agent_card)

    assert signed_card.signatures is not None
    assert len(signed_card.signatures) == 1
    signature = signed_card.signatures[0]
    assert signature.protected is not None
    assert signature.signature is not None

    verifier = signing.create_signature_verifier(
        create_key_provider(public_key), ['HS256', 'HS384', 'ES256', 'RS256']
    )
    try:
        verifier(signed_card)
    except signing.InvalidSignaturesError:
        pytest.fail('Signature verification failed with correct key')

    # Verify with wrong key
    verifier_wrong_key = signing.create_signature_verifier(
        create_key_provider(public_key_error),
        ['HS256', 'HS384', 'ES256', 'RS256'],
    )
    with pytest.raises(signing.InvalidSignaturesError):
        verifier_wrong_key(signed_card)
