"""Algorithm-agility vectors for Agent Card signature verification.

`create_signature_verifier` takes an explicit list of acceptable algorithms so
that the algorithm named in a signature's protected header cannot steer
verification outside the set the caller chose. That list is the seam a future
algorithm extends, so these vectors pin its behavior. A card signed with an
algorithm outside the list is rejected, a symmetric signature presented against
an asymmetric key is rejected, the unsecured ``none`` algorithm is rejected
even when the caller names it, a header with no ``alg`` is rejected, and an
algorithm that is named but not registered, such as the FIPS 204 ML-DSA family,
is rejected even when it appears in both the header and the list. Adding a
post-quantum algorithm is therefore additive and stays gated behind both a
caller opt-in and a real registered implementation.
"""

import json

from typing import Any

import pytest

from a2a.types.a2a_pb2 import (
    AgentCapabilities,
    AgentCard,
    AgentCardSignature,
    AgentInterface,
    AgentSkill,
)
from a2a.utils import signing
from cryptography.hazmat.primitives.asymmetric import ec
from jwt.utils import base64url_decode, base64url_encode


def _key_provider(verification_key: Any):
    def provide(kid: str | None, jku: str | None) -> Any:
        return verification_key

    return provide


def _fresh_card() -> AgentCard:
    return AgentCard(
        name='Test Agent',
        description='A test agent',
        supported_interfaces=[
            AgentInterface(
                url='http://localhost',
                protocol_binding='HTTP+JSON',
                protocol_version='1.0',
            )
        ],
        version='1.0.0',
        capabilities=AgentCapabilities(push_notifications=True),
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


def _rewrite_protected_header(
    signature: AgentCardSignature, mutate: Any
) -> None:
    """Applies ``mutate`` to the decoded protected header and re-encodes it.

    The payload and signature bytes are left untouched, so only the header the
    verifier reads to pick an algorithm changes.
    """
    header = json.loads(
        base64url_decode(signature.protected.encode('utf-8')).decode('utf-8')
    )
    mutate(header)
    signature.protected = base64url_encode(
        json.dumps(header).encode('utf-8')
    ).decode('utf-8')


@pytest.fixture
def es256_keys() -> tuple[
    ec.EllipticCurvePrivateKey, ec.EllipticCurvePublicKey
]:
    private_key = ec.generate_private_key(ec.SECP256R1())
    return private_key, private_key.public_key()


@pytest.fixture
def es256_signed_card(
    es256_keys: tuple[ec.EllipticCurvePrivateKey, ec.EllipticCurvePublicKey],
) -> AgentCard:
    private_key, _ = es256_keys
    signer = signing.create_agent_card_signer(
        signing_key=private_key,
        protected_header={
            'alg': 'ES256',
            'kid': 'key1',
            'jku': None,
            'typ': 'JOSE',
        },
    )
    return signer(_fresh_card())


def test_es256_card_verifies_when_es256_is_allowed(
    es256_keys: tuple[ec.EllipticCurvePrivateKey, ec.EllipticCurvePublicKey],
    es256_signed_card: AgentCard,
) -> None:
    """A genuine ES256 signature verifies when ES256 is on the allow-list."""
    _, public_key = es256_keys
    verifier = signing.create_signature_verifier(
        _key_provider(public_key), ['ES256', 'RS256']
    )
    verifier(es256_signed_card)


@pytest.mark.parametrize(
    'allowed',
    [
        pytest.param(['RS256'], id='rs256-only'),
        pytest.param(['HS256'], id='hs256-only'),
        pytest.param(['HS256', 'HS384', 'RS256'], id='several-without-es256'),
    ],
)
def test_es256_card_rejected_when_es256_not_allowed(
    es256_keys: tuple[ec.EllipticCurvePrivateKey, ec.EllipticCurvePublicKey],
    es256_signed_card: AgentCard,
    allowed: list[str],
) -> None:
    """An algorithm absent from the allow-list is rejected with the right key."""
    _, public_key = es256_keys
    verifier = signing.create_signature_verifier(
        _key_provider(public_key), allowed
    )
    with pytest.raises(signing.InvalidSignaturesError):
        verifier(es256_signed_card)


def test_symmetric_signature_rejected_against_asymmetric_key(
    es256_keys: tuple[ec.EllipticCurvePrivateKey, ec.EllipticCurvePublicKey],
) -> None:
    """An HS256 signature does not verify against a public key under an asymmetric allow-list.

    This is the algorithm-confusion case the allow-list exists to stop. The
    header asks for a symmetric algorithm while the caller allows only
    asymmetric ones, so the two never meet.
    """
    _, public_key = es256_keys
    hmac_signer = signing.create_agent_card_signer(
        signing_key='a-shared-secret-of-sufficient-length-000000000000',
        protected_header={'alg': 'HS256', 'kid': 'key1', 'jku': None},
    )
    hs_card = hmac_signer(_fresh_card())
    verifier = signing.create_signature_verifier(
        _key_provider(public_key), ['ES256', 'RS256']
    )
    with pytest.raises(signing.InvalidSignaturesError):
        verifier(hs_card)


@pytest.mark.parametrize(
    'allowed',
    [
        pytest.param(['ES256'], id='none-not-allowed'),
        pytest.param(['none'], id='none-allowed'),
        pytest.param(['ES256', 'none'], id='none-among-allowed'),
    ],
)
def test_unsecured_none_algorithm_rejected(
    es256_keys: tuple[ec.EllipticCurvePrivateKey, ec.EllipticCurvePublicKey],
    allowed: list[str],
) -> None:
    """A signature that claims the unsecured ``none`` algorithm never verifies."""
    _, public_key = es256_keys
    card = _fresh_card()
    canonical_payload = signing._canonicalize_agent_card(card)
    protected = base64url_encode(
        json.dumps({'alg': 'none', 'kid': 'key1'}).encode('utf-8')
    ).decode('utf-8')
    card.signatures.append(
        AgentCardSignature(protected=protected, signature='')
    )
    verifier = signing.create_signature_verifier(
        _key_provider(public_key), allowed
    )
    assert canonical_payload
    with pytest.raises(signing.InvalidSignaturesError):
        verifier(card)


def test_missing_alg_in_protected_header_rejected(
    es256_keys: tuple[ec.EllipticCurvePrivateKey, ec.EllipticCurvePublicKey],
    es256_signed_card: AgentCard,
) -> None:
    """A protected header with no ``alg`` is rejected even over genuine signature bytes."""
    _, public_key = es256_keys
    _rewrite_protected_header(
        es256_signed_card.signatures[0], lambda h: h.pop('alg', None)
    )
    verifier = signing.create_signature_verifier(
        _key_provider(public_key), ['ES256']
    )
    with pytest.raises(signing.InvalidSignaturesError):
        verifier(es256_signed_card)


@pytest.mark.parametrize(
    'allowed',
    [
        pytest.param(['ES256'], id='pq-name-not-allowed'),
        pytest.param(['ML-DSA-65'], id='pq-name-allowed'),
        pytest.param(['ES256', 'ML-DSA-65'], id='pq-name-among-allowed'),
    ],
)
def test_unregistered_algorithm_name_rejected(
    es256_keys: tuple[ec.EllipticCurvePrivateKey, ec.EllipticCurvePublicKey],
    es256_signed_card: AgentCard,
    allowed: list[str],
) -> None:
    """An algorithm that is named but not registered does not verify.

    The header advertises ML-DSA-65 over otherwise genuine signature bytes.
    No such algorithm is registered, so verification fails even when the name
    is on the allow-list. Post-quantum support therefore cannot arrive by name
    alone and stays gated behind a real registered implementation.
    """
    _, public_key = es256_keys
    _rewrite_protected_header(
        es256_signed_card.signatures[0],
        lambda h: h.__setitem__('alg', 'ML-DSA-65'),
    )
    verifier = signing.create_signature_verifier(
        _key_provider(public_key), allowed
    )
    with pytest.raises(signing.InvalidSignaturesError):
        verifier(es256_signed_card)
