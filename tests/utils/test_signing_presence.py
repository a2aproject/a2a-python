"""Agent Card field-presence rules from A2A 1.0 section 8.4.1.

Expected objects are explicit protocol vectors, independent of Protobuf's
default JSON serializer. RFC 8785 and stdlib HMAC provide independent oracles
for the canonical bytes and signatures in both public signing directions.
"""

import base64
import copy
import hashlib
import hmac

from typing import Any

import pytest
import rfc8785

from a2a.types import AgentCard, AgentCardSignature
from a2a.utils import signing
from google.protobuf.json_format import ParseDict


def _card_object() -> dict[str, Any]:
    return {
        'name': 'Presence',
        'description': 'Presence rules',
        'version': '1',
        'capabilities': {'streaming': True},
        'supportedInterfaces': [
            {
                'url': 'https://example.com/a2a',
                'protocolBinding': 'JSONRPC',
                'protocolVersion': '1.0',
            }
        ],
        'defaultInputModes': ['text/plain'],
        'defaultOutputModes': ['text/plain'],
        'skills': [
            {
                'id': 'presence',
                'name': 'Presence',
                'description': 'Presence skill',
                'tags': ['presence'],
            }
        ],
    }


def _presence_vector(case: str) -> tuple[dict[str, Any], dict[str, Any]]:
    card = _card_object()
    if case == 'required-empty-message':
        card['capabilities'] = {}
    elif case == 'required-empty-scalar':
        card['description'] = ''
    elif case == 'required-nested-default':
        card['supportedInterfaces'][0]['protocolVersion'] = ''
    elif case == 'explicit-optional-empty':
        card['documentationUrl'] = ''
        card['iconUrl'] = ''
    elif case == 'explicit-optional-false':
        card['capabilities'] = {'streaming': False}
    elif case == 'nested-empty-message':
        card['provider'] = {'organization': '', 'url': ''}
    elif case == 'json-empties':
        card['capabilities']['extensions'] = [
            {
                'uri': 'https://example.com/ext',
                'params': {
                    'emptyObject': {},
                    'emptyArray': [],
                    'emptyString': '',
                    'null': None,
                    'false': False,
                    'zero': 0,
                    'list': ['', {}, [], None, False, 0],
                    'fields': {'userKey': {}},
                },
            }
        ]
    elif case == 'explicit-empty-params':
        card['capabilities']['extensions'] = [
            {'uri': 'https://example.com/ext', 'params': {}}
        ]
    elif case == 'map-values-and-required-defaults':
        card['securitySchemes'] = {
            'oauth': {
                'oauth2SecurityScheme': {
                    'flows': {
                        'authorizationCode': {
                            'authorizationUrl': '',
                            'tokenUrl': '',
                            'scopes': {'read': ''},
                        }
                    }
                }
            }
        }
        card['securityRequirements'] = [{'schemes': {'oauth': {}}}]
    elif case == 'required-empty-arrays':
        card['defaultInputModes'] = []
        card['defaultOutputModes'] = []
        card['skills'][0]['tags'] = []
    elif case == 'implicit-optional-defaults':
        expected = copy.deepcopy(card)
        card['supportedInterfaces'][0]['tenant'] = ''
        card['skills'][0]['examples'] = []
        card['securitySchemes'] = {}
        return card, expected
    elif case == 'implicit-extension-defaults':
        card['capabilities']['extensions'] = [
            {'uri': '', 'description': '', 'required': False}
        ]
        expected = copy.deepcopy(card)
        expected['capabilities']['extensions'] = [{}]
        return card, expected
    elif case == 'unset-required-fields':
        expected = copy.deepcopy(card)
        del card['capabilities']
        del card['description']
        expected['capabilities'] = {}
        expected['description'] = ''
        return card, expected
    else:
        assert case == 'positive-control'
    return card, copy.deepcopy(card)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')


@pytest.mark.parametrize(
    'case',
    [
        'positive-control',
        'required-empty-message',
        'required-empty-scalar',
        'required-nested-default',
        'explicit-optional-empty',
        'explicit-optional-false',
        'nested-empty-message',
        'json-empties',
        'explicit-empty-params',
        'map-values-and-required-defaults',
        'required-empty-arrays',
        'implicit-optional-defaults',
        'implicit-extension-defaults',
        'unset-required-fields',
    ],
)
def test_public_signing_obeys_field_presence(case: str) -> None:
    served, expected = _presence_vector(case)
    card = ParseDict(served, AgentCard())
    before = card.SerializeToString()
    payload = rfc8785.dumps(expected)
    key = b'presence-vector-test-key-with-at-least-32-bytes'
    signature = signing.create_agent_card_signer(
        key, {'alg': 'HS256', 'kid': 'presence'}
    )(card).signatures[0]
    signing_input = f'{signature.protected}.{_b64(payload)}'.encode('ascii')
    expected_signature = _b64(
        hmac.new(key, signing_input, hashlib.sha256).digest()
    )
    assert signature.signature == expected_signature

    independently_signed = ParseDict(served, AgentCard())
    independently_signed.signatures.append(
        AgentCardSignature(
            protected=signature.protected,
            signature=expected_signature,
        )
    )
    verifier = signing.create_signature_verifier(
        lambda kid, jku: key, ['HS256']
    )
    verifier(independently_signed)

    # The only permitted signer mutation is appending the detached signature.
    card.ClearField('signatures')
    assert card.SerializeToString() == before


@pytest.mark.parametrize('field', ['documentation_url', 'icon_url'])
def test_explicit_empty_optional_field_is_signed(field: str) -> None:
    key = b'presence-vector-test-key-with-at-least-32-bytes'
    card = ParseDict(_card_object(), AgentCard())
    signing.create_agent_card_signer(key, {'alg': 'HS256', 'kid': 'presence'})(
        card
    )
    verifier = signing.create_signature_verifier(
        lambda kid, jku: key, ['HS256']
    )
    verifier(card)
    setattr(card, field, '')
    assert card.HasField(field)
    with pytest.raises(signing.InvalidSignaturesError):
        verifier(card)
