import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from jwt.utils import base64url_encode
from typing import Any

from a2a.types.a2a_pb2 import (
    AgentCard,
    AgentCapabilities,
    AgentSkill,
    AgentCardSignature,
    AgentInterface,
)
from a2a.utils import signing


def create_key_provider(verification_key: Any):
    """Creates a key provider function for testing."""

    def key_provider(kid: str | None, jku: str | None):
        return verification_key

    return key_provider


@pytest.fixture
def sample_agent_card() -> AgentCard:
    return AgentCard(
        name='Test Agent',
        description='A test agent',
        supported_interfaces=[
            AgentInterface(
                url='http://localhost',
                protocol_binding='HTTP+JSON',
            )
        ],
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
    key = 'key12345'
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

    verifier = signing.create_signature_verifier(
        create_key_provider(key), ['HS256', 'HS384', 'ES256', 'RS256']
    )
    try:
        verifier(signed_card)
    except signing.InvalidSignaturesError:
        pytest.fail('Signature verification failed with correct key')

    verifier_wrong_key = signing.create_signature_verifier(
        create_key_provider(wrong_key), ['HS256', 'HS384', 'ES256', 'RS256']
    )
    with pytest.raises(signing.InvalidSignaturesError):
        verifier_wrong_key(signed_card)


def test_signer_and_verifier_symmetric_multiple_signatures(
    sample_agent_card: AgentCard,
):
    """Test the agent card signing and verification process with symmetric key encryption.
    This test adds a signature to the AgentCard before signing."""
    encoded_header = base64url_encode(
        b'{"alg": "HS256", "kid": "old_key"}'
    ).decode('utf-8')
    sample_agent_card.signatures.extend(
        [
            AgentCardSignature(
                protected=encoded_header, signature='old_signature'
            )
        ]
    )
    key = 'key12345'
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

    verifier = signing.create_signature_verifier(
        create_key_provider(key), ['HS256', 'HS384', 'ES256', 'RS256']
    )
    try:
        verifier(signed_card)
    except signing.InvalidSignaturesError:
        pytest.fail('Signature verification failed with correct key')

    verifier_wrong_key = signing.create_signature_verifier(
        create_key_provider(wrong_key), ['HS256', 'HS384', 'ES256', 'RS256']
    )
    with pytest.raises(signing.InvalidSignaturesError):
        verifier_wrong_key(signed_card)


def test_signer_and_verifier_asymmetric(sample_agent_card: AgentCard):
    """Test the agent card signing and verification process with an asymmetric key encryption."""
    private_key = ec.generate_private_key(ec.SECP256R1())
    public_key = private_key.public_key()
    private_key_error = ec.generate_private_key(ec.SECP256R1())
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

    verifier_wrong_key = signing.create_signature_verifier(
        create_key_provider(public_key_error),
        ['HS256', 'HS384', 'ES256', 'RS256'],
    )
    with pytest.raises(signing.InvalidSignaturesError):
        verifier_wrong_key(signed_card)


def test_canonicalize_agent_card(sample_agent_card: AgentCard):
    """Test canonicalize_agent_card with defaults, optionals, and exceptions.

    - extensions is omitted as it's not set and optional.
    - protocolVersion is included because it's always added by canonicalize_agent_card.
    - signatures should be omitted.
    """
    expected_jcs = (
        '{"capabilities":{"pushNotifications":true},'
        '"defaultInputModes":["text/plain"],"defaultOutputModes":["text/plain"],'
        '"description":"A test agent","name":"Test Agent",'
        '"skills":[{"description":"A test skill","id":"skill1","name":"Test Skill","tags":["test"]}],'
        '"supportedInterfaces":[{"protocolBinding":"HTTP+JSON","url":"http://localhost"}],'
        '"version":"1.0.0"}'
    )
    result = signing._canonicalize_agent_card(sample_agent_card)
    assert result == expected_jcs


def test_canonicalize_agent_card_preserves_false_capability(
    sample_agent_card: AgentCard,
):
    """Regression #692: streaming=False must not be stripped from canonical JSON."""
    sample_agent_card.capabilities.streaming = False
    result = signing._canonicalize_agent_card(sample_agent_card)
    assert '"streaming":false' in result


@pytest.mark.parametrize(
    'input_val',
    [
        pytest.param({'a': ''}, id='empty-string'),
        pytest.param({'a': []}, id='empty-list'),
        pytest.param({'a': {}}, id='empty-dict'),
        pytest.param({'a': {'b': []}}, id='nested-empty'),
        pytest.param({'a': '', 'b': [], 'c': {}}, id='all-empties'),
        pytest.param({'a': {'b': {'c': ''}}}, id='deeply-nested'),
    ],
)
def test_clean_empty_removes_empties(input_val):
    """_clean_empty removes empty strings, lists, and dicts recursively."""
    assert signing._clean_empty(input_val) is None


def test_clean_empty_top_level_list_becomes_none():
    """Top-level list that becomes empty after cleaning should return None."""
    assert signing._clean_empty(['', {}, []]) is None


@pytest.mark.parametrize(
    'input_val,expected',
    [
        pytest.param({'retries': 0}, {'retries': 0}, id='int-zero'),
        pytest.param({'enabled': False}, {'enabled': False}, id='bool-false'),
        pytest.param({'score': 0.0}, {'score': 0.0}, id='float-zero'),
        pytest.param([0, 1, 2], [0, 1, 2], id='zero-in-list'),
        pytest.param([False, True], [False, True], id='false-in-list'),
        pytest.param(
            {'config': {'max_retries': 0, 'name': 'agent'}},
            {'config': {'max_retries': 0, 'name': 'agent'}},
            id='nested-zero',
        ),
    ],
)
def test_clean_empty_preserves_falsy_values(input_val, expected):
    """_clean_empty preserves legitimate falsy values (0, False, 0.0)."""
    assert signing._clean_empty(input_val) == expected


@pytest.mark.parametrize(
    'input_val,expected',
    [
        pytest.param(
            {'count': 0, 'label': '', 'items': []},
            {'count': 0},
            id='falsy-with-empties',
        ),
        pytest.param(
            {'a': 0, 'b': 'hello', 'c': False, 'd': ''},
            {'a': 0, 'b': 'hello', 'c': False},
            id='mixed-types',
        ),
        pytest.param(
            {'name': 'agent', 'retries': 0, 'tags': [], 'desc': ''},
            {'name': 'agent', 'retries': 0},
            id='realistic-mixed',
        ),
    ],
)
def test_clean_empty_mixed(input_val, expected):
    """_clean_empty handles mixed empty and falsy values correctly."""
    assert signing._clean_empty(input_val) == expected


def test_clean_empty_does_not_mutate_input():
    """_clean_empty should not mutate the original input object."""
    original = {'a': '', 'b': 1, 'c': {'d': ''}}
    original_copy = {
        'a': '',
        'b': 1,
        'c': {'d': ''},
    }

    signing._clean_empty(original)

    assert original == original_copy
