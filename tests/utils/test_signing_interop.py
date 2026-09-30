"""Cross-SDK Agent Card signature tests.

`signing_interop_vectors.json` holds one card signed four ways: by this SDK,
by @a2a-js/sdk, by a2a-go and by an independent reference signer. The card
carries every REQUIRED field and no field at its default value, so the
canonical readings in use today agree on its bytes, and those expectations
hold however a2aproject/A2A#2122 is settled: a card another SDK signed still
verifies here.

The frozen production card below is different. It carries
`securityRequirements: [{}]`, a nested empty value whose canonical form is
part of the #2122 question, so its test pins today's bytes, the ones this SDK
and @a2a-js/sdk agree on, as agreed in #1278. It needs updating if #2122
settles the other way.
"""

import hashlib
import json

from pathlib import Path

import pytest

from a2a.types import AgentCard
from a2a.utils import signing
from google.protobuf import json_format
from jwt import PyJWK


_HERE = Path(__file__).parent
_VECTORS = json.loads(
    (_HERE / 'signing_interop_vectors.json').read_text(encoding='utf-8')
)
_ACCEPT = [v for v in _VECTORS['vectors'] if v['disposition'] == 'MUST-ACCEPT']
_REJECT = [v for v in _VECTORS['vectors'] if v['disposition'] == 'MUST-REJECT']

# A card served in production by HORIZON SHIELD, signed by @a2a-js/sdk with two
# ES256 signatures, frozen on 2026-09-28 and published with its key set in
# a2aproject/a2a-go#445. It carries fields this SDK's proto does not have
# (url, protocolVersion, preferredTransport, compensation), so it is parsed
# leniently. The anchors are the ones used for the independent verification
# in #1278.
_GATE_CARD = _HERE / 'signing_gate_card_20260928.json'
_GATE_JWKS = _HERE / 'signing_gate_jwks_20260928.json'
_GATE_CARD_SHA256 = (
    '2df33ff120745a7f46b8afc1378d72b437b3f05b4db61c8d8b3655c581584455'
)
_GATE_JWKS_SHA256 = (
    '692fd49da681cc0d0bda9ad46963fa5f45957a59d10f84998e9b2e4061209d3c'
)
_GATE_CANONICAL_LEN = 6410
_GATE_CANONICAL_SHA256 = (
    'c5d5384a19f3a15c761ff93bf9fec892ec99615b6356d7675fda5b79286b59b1'
)


def _key_provider(jwks: dict):
    def provide(kid: str | None, jku: str | None) -> PyJWK:
        for key in jwks['keys']:
            if key['kid'] == kid:
                return PyJWK(key)
        raise ValueError(f'kid not in key set: {kid}')

    return provide


def _card(served: dict, lenient: bool = False) -> AgentCard:
    return json_format.ParseDict(
        served, AgentCard(), ignore_unknown_fields=lenient
    )


_VERIFY = signing.create_signature_verifier(
    _key_provider({'keys': [_VECTORS['test_key']['jwk']]}), ['ES256']
)


def test_vector_corpus_is_complete():
    """The corpus must be whole; a partially loaded corpus passes vacuously."""
    assert len(_ACCEPT) == _VECTORS['counts']['accept'] == 4
    assert len(_REJECT) == _VECTORS['counts']['reject'] == 1
    assert {v['signer'] for v in _ACCEPT} >= {
        'a2a-sdk 1.2.1',
        '@a2a-js/sdk 1.3.0',
        'a2a-go main 534a60fc',
    }


@pytest.mark.parametrize('vector', _ACCEPT, ids=lambda v: v['id'])
def test_card_signed_by_any_sdk_verifies(vector):
    """A card signed by any SDK verifies here."""
    _VERIFY(_card(vector['served_card']))


@pytest.mark.parametrize('vector', _ACCEPT, ids=lambda v: v['id'])
def test_card_canonical_bytes_are_the_shared_form(vector):
    """The bytes every SDK and every reading agree on, byte for byte."""
    canonical = signing._canonicalize_agent_card(_card(vector['served_card']))
    assert canonical.encode('utf-8').hex() == vector['canonical_utf8_hex']


@pytest.mark.parametrize('vector', _REJECT, ids=lambda v: v['id'])
def test_card_edited_after_signing_is_rejected(vector):
    """A signature over other bytes must not verify."""
    with pytest.raises(signing.InvalidSignaturesError):
        _VERIFY(_card(vector['served_card']))


def test_frozen_js_signed_production_card():
    """A card @a2a-js/sdk signed in production verifies, bytes unchanged."""
    card_bytes = _GATE_CARD.read_bytes()
    jwks_bytes = _GATE_JWKS.read_bytes()
    assert hashlib.sha256(card_bytes).hexdigest() == _GATE_CARD_SHA256
    assert hashlib.sha256(jwks_bytes).hexdigest() == _GATE_JWKS_SHA256

    card = _card(json.loads(card_bytes), lenient=True)
    canonical = signing._canonicalize_agent_card(card).encode('utf-8')
    assert len(canonical) == _GATE_CANONICAL_LEN
    assert hashlib.sha256(canonical).hexdigest() == _GATE_CANONICAL_SHA256

    verify = signing.create_signature_verifier(
        _key_provider(json.loads(jwks_bytes)), ['ES256']
    )
    verify(card)
