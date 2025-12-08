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
from a2a.utils.signing import (
    canonicalize_agent_card,
    create_agent_card_signer,
    create_signature_verifier,
)
from typing import Any
from jose.backends.base import Key
from jose.exceptions import JOSEError
from jose.utils import base64url_encode

import pytest
from cryptography.hazmat.primitives import asymmetric


def create_key_provider(verification_key: str | bytes | dict[str, Any] | Key):
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

    agent_card_signer = create_agent_card_signer(
        signing_key=key, alg='HS384', kid='key1'
    )
    signed_card = agent_card_signer(sample_agent_card)

    assert signed_card.signatures is not None
    assert len(signed_card.signatures) == 1
    signature = signed_card.signatures[0]
    assert signature.protected is not None
    assert signature.signature is not None

    # Verify the signature
    verifier = create_signature_verifier(create_key_provider(key))
    try:
        verifier(signed_card)
    except JOSEError:
        pytest.fail('Signature verification failed with correct key')

    # Verify with wrong key
    verifier_wrong_key = create_signature_verifier(
        create_key_provider(wrong_key)
    )
    with pytest.raises(JOSEError):
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

    agent_card_signer = create_agent_card_signer(
        signing_key=key, alg='HS384', kid='key1'
    )
    signed_card = agent_card_signer(sample_agent_card)

    assert signed_card.signatures is not None
    assert len(signed_card.signatures) == 2
    signature = signed_card.signatures[1]
    assert signature.protected is not None
    assert signature.signature is not None

    # Verify the signature
    verifier = create_signature_verifier(create_key_provider(key))
    try:
        verifier(signed_card)
    except JOSEError:
        pytest.fail('Signature verification failed with correct key')

    # Verify with wrong key
    verifier_wrong_key = create_signature_verifier(
        create_key_provider(wrong_key)
    )
    with pytest.raises(JOSEError):
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

    agent_card_signer = create_agent_card_signer(
        signing_key=private_key, alg='ES256', kid='key1'
    )
    signed_card = agent_card_signer(sample_agent_card)

    assert signed_card.signatures is not None
    assert len(signed_card.signatures) == 1
    signature = signed_card.signatures[0]
    assert signature.protected is not None
    assert signature.signature is not None

    verifier = create_signature_verifier(create_key_provider(public_key))
    try:
        verifier(signed_card)
    except JOSEError:
        pytest.fail('Signature verification failed with correct key')

    # Verify with wrong key
    verifier_wrong_key = create_signature_verifier(
        create_key_provider(public_key_error)
    )
    with pytest.raises(JOSEError):
        verifier_wrong_key(signed_card)


def test_canonicalize_agent_card(
    sample_agent_card: AgentCard,
):
    """Test canonicalize_agent_card with defaults, optionals, and exceptions.

    - extensions is omitted as it's not set and optional.
    - protocolVersion is included because it's always added by canonicalize_agent_card.
    - signatures should be omitted.
    """
    sample_agent_card.signatures = (
        [{'protected': 'protected_header', 'signature': 'test_signature'}],
    )
    expected_jcs = (
        '{"capabilities":{"pushNotifications":true},'
        '"defaultInputModes":["text/plain"],"defaultOutputModes":["text/plain"],'
        '"description":"A test agent","name":"Test Agent","protocolVersion":"0.3.0",'
        '"skills":[{"description":"A test skill","id":"skill1","name":"Test Skill","tags":["test"]}],'
        '"url":"http://localhost","version":"1.0.0"}'
    )
    result = canonicalize_agent_card(sample_agent_card)
    assert result == expected_jcs
