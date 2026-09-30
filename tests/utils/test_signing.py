from typing import Any

import pytest

from a2a.types.a2a_pb2 import (
    APIKeySecurityScheme,
    AgentCapabilities,
    AgentCard,
    AgentCardSignature,
    AgentExtension,
    AgentInterface,
    AgentProvider,
    AgentSkill,
    AuthorizationCodeOAuthFlow,
    OAuth2SecurityScheme,
    OAuthFlows,
    SecurityRequirement,
    SecurityScheme,
    StringList,
)
from a2a.utils import signing
from cryptography.hazmat.primitives.asymmetric import ec
from google.protobuf.json_format import MessageToDict
from jwt import api_jws
from jwt.utils import base64url_encode


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
    - protocolVersion is REQUIRED on AgentInterface, so it is kept at its
      default value (A2A specification section 8.4.1).
    - signatures should be omitted.
    """
    expected_jcs = (
        '{"capabilities":{"pushNotifications":true},'
        '"defaultInputModes":["text/plain"],"defaultOutputModes":["text/plain"],'
        '"description":"A test agent","name":"Test Agent",'
        '"skills":[{"description":"A test skill","id":"skill1","name":"Test Skill","tags":["test"]}],'
        '"supportedInterfaces":[{"protocolBinding":"HTTP+JSON","protocolVersion":"","url":"http://localhost"}],'
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


@pytest.fixture
def full_agent_card() -> AgentCard:
    """A card that exercises nested messages, maps and free-form values."""
    card = AgentCard(
        name='Full Agent',
        description='A card that exercises nested messages',
        supported_interfaces=[
            AgentInterface(
                url='https://example.com/a2a/v1',
                protocol_binding='JSONRPC',
                protocol_version='1.0',
                tenant='',
            )
        ],
        provider=AgentProvider(
            url='https://example.com', organization='Example'
        ),
        version='1.0.0',
        capabilities=AgentCapabilities(
            streaming=False,
            extensions=[
                AgentExtension(uri='https://example.com/ext/1'),
                AgentExtension(uri='https://example.com/ext/2', description=''),
            ],
        ),
        security_schemes={
            'key': SecurityScheme(
                api_key_security_scheme=APIKeySecurityScheme(
                    location='header', name='X-API-Key', description=''
                )
            ),
            'oauth': SecurityScheme(
                oauth2_security_scheme=OAuth2SecurityScheme(
                    flows=OAuthFlows(
                        authorization_code=AuthorizationCodeOAuthFlow(
                            authorization_url='https://example.com/auth',
                            token_url='https://example.com/token',
                            scopes={'read': 'Read access'},
                        )
                    )
                )
            ),
        },
        security_requirements=[
            SecurityRequirement(schemes={'oauth': StringList(list=['read'])}),
            SecurityRequirement(),
        ],
        default_input_modes=['text/plain'],
        default_output_modes=['text/plain'],
        skills=[
            AgentSkill(
                id='skill1',
                name='Skill',
                description='A skill',
                tags=['test'],
                examples=[],
            )
        ],
        icon_url='',
    )
    card.capabilities.extensions[0].params.update(
        {'empty': '', 'nested': {'list': [], 'kept': 0}}
    )
    return card


def test_required_fields_are_read_from_descriptors():
    """REQUIRED comes from `google.api.field_behavior`, not from a name list."""
    card = AgentCard.DESCRIPTOR
    required = {f.json_name for f in card.fields if signing._is_required(f)}
    assert required == {
        'name',
        'description',
        'supportedInterfaces',
        'version',
        'capabilities',
        'defaultInputModes',
        'defaultOutputModes',
        'skills',
    }
    skill = AgentSkill.DESCRIPTOR
    assert {f.json_name for f in skill.fields if signing._is_required(f)} == {
        'id',
        'name',
        'description',
        'tags',
    }


def test_implicit_presence_fields_are_not_all_required():
    """Fields that the blanket `always_print` flag emits stay non-REQUIRED."""
    tenant = AgentInterface.DESCRIPTOR.fields_by_name['tenant']
    ext_required = AgentExtension.DESCRIPTOR.fields_by_name['required']
    ext_list = AgentCapabilities.DESCRIPTOR.fields_by_name['extensions']
    assert not signing._is_required(tenant)
    assert not signing._is_required(ext_required)
    assert not signing._is_required(ext_list)


def test_clean_message_matches_clean_empty_on_full_card(
    full_agent_card: AgentCard,
):
    """With no empty REQUIRED field, descriptor-aware cleaning changes nothing."""
    card_dict = MessageToDict(full_agent_card)
    assert signing._clean_message(
        card_dict, AgentCard.DESCRIPTOR
    ) == signing._clean_empty(card_dict)


def test_canonicalize_full_card_prunes_optional_defaults(
    full_agent_card: AgentCard,
):
    """Optional and free-form empty values are pruned at every level."""
    result = signing._canonicalize_agent_card(full_agent_card)
    assert '"tenant"' not in result
    assert '"iconUrl"' not in result
    assert '"examples"' not in result
    assert '"empty"' not in result
    assert '"list"' in result  # the StringList inside the security requirement
    assert '"params":{"nested":{"kept":0}}' in result
    assert '"streaming":false' in result
    # The empty SecurityRequirement element is dropped, the other one stays.
    assert (
        '"securityRequirements":[{"schemes":{"oauth":{"list":["read"]}}}]'
        in result
    )


def test_clean_message_bounds_depth():
    """Descriptor-aware cleaning keeps the depth bound of `_clean_empty`."""
    nested: dict[str, Any] = {}
    cursor = nested
    for _ in range(signing.MAX_DEPTH + 5):
        cursor['a'] = {}
        cursor = cursor['a']
    card_dict = {'capabilities': {'extensions': [{'params': nested}]}}
    with pytest.raises(signing.CanonicalizationError):
        signing._clean_message(card_dict, AgentCard.DESCRIPTOR)


def _reachable_messages(descriptor, seen=None):
    seen = {} if seen is None else seen
    if descriptor.full_name in seen or signing._is_well_known(descriptor):
        return seen
    seen[descriptor.full_name] = descriptor
    for field in descriptor.fields:
        message_type = field.message_type
        if message_type is None:
            continue
        if signing._is_map(field):
            message_type = message_type.fields_by_name['value'].message_type
            if message_type is None:
                continue
        _reachable_messages(message_type, seen)
    return seen


def _example_card(**overrides: Any) -> AgentCard:
    fields: dict[str, Any] = {
        'name': 'Example Agent',
        'description': 'An example',
        'version': '1.0.0',
        'supported_interfaces': [
            AgentInterface(
                url='https://example.com/a2a/v1',
                protocol_binding='JSONRPC',
                protocol_version='1.0',
            )
        ],
        'capabilities': AgentCapabilities(
            streaming=False, push_notifications=False
        ),
        'default_input_modes': ['text/plain'],
        'default_output_modes': ['text/plain'],
        'skills': [
            AgentSkill(
                id='skill1', name='Skill', description='A skill', tags=['t']
            )
        ],
    }
    fields.update(overrides)
    return AgentCard(**fields)


def test_canonicalize_keeps_required_fields_at_default():
    """Section 8.4.1: `description: ""` and `skills: []` stay in the payload.

    The card is the worked example of section 8.4.1 with the remaining
    REQUIRED fields of AgentCard filled in.
    """
    card = _example_card(description='', skills=[])
    card.capabilities.extensions.extend([])
    expected_jcs = (
        '{"capabilities":{"pushNotifications":false,"streaming":false},'
        '"defaultInputModes":["text/plain"],"defaultOutputModes":["text/plain"],'
        '"description":"","name":"Example Agent","skills":[],'
        '"supportedInterfaces":[{"protocolBinding":"JSONRPC",'
        '"protocolVersion":"1.0","url":"https://example.com/a2a/v1"}],'
        '"version":"1.0.0"}'
    )
    assert signing._canonicalize_agent_card(card) == expected_jcs


@pytest.mark.parametrize(
    ('overrides', 'kept'),
    [
        pytest.param({'description': ''}, '"description":""', id='description'),
        pytest.param({'skills': []}, '"skills":[]', id='skills'),
        pytest.param({'version': ''}, '"version":""', id='version'),
        pytest.param(
            {'default_input_modes': []},
            '"defaultInputModes":[]',
            id='default-input-modes',
        ),
        pytest.param(
            {
                'skills': [
                    AgentSkill(id='s', name='Skill', description='A skill')
                ]
            },
            '"tags":[]',
            id='skill-tags',
        ),
    ],
)
def test_canonicalize_keeps_each_empty_required_field(
    overrides: dict[str, Any], kept: str
):
    assert kept in signing._canonicalize_agent_card(_example_card(**overrides))


def test_canonicalize_still_prunes_non_required_defaults():
    """Implicit-presence fields that are not REQUIRED stay out of the payload."""
    card = _example_card(description='', skills=[])
    card.supported_interfaces[0].tenant = ''
    card.capabilities.extensions.add(
        uri='https://example.com/ext', required=False
    )
    result = signing._canonicalize_agent_card(card)
    assert '"tenant"' not in result
    assert '"required"' not in result
    assert '"extensions":[{"uri":"https://example.com/ext"}]' in result
    assert '"securitySchemes"' not in result
    assert '"securityRequirements"' not in result


def test_canonicalize_keeps_an_unset_required_message():
    """A REQUIRED message is present even when it was never set."""
    card = _example_card()
    card.ClearField('capabilities')
    assert not card.HasField('capabilities')
    assert '"capabilities":{}' in signing._canonicalize_agent_card(card)


def test_canonicalize_keeps_a_set_but_empty_required_message():
    card = _example_card(capabilities=AgentCapabilities())
    assert '"capabilities":{}' in signing._canonicalize_agent_card(card)


def test_every_required_field_has_a_default_this_module_can_emit():
    """Fails when a REQUIRED field of an unhandled scalar type is added."""
    unhandled = []
    for descriptor in _reachable_messages(AgentCard.DESCRIPTOR).values():
        for field in descriptor.fields:
            if not signing._is_required(field):
                continue
            if signing._required_default(field) is None:
                unhandled.append(f'{descriptor.name}.{field.name}')
    assert unhandled == []


def test_required_messages_have_no_required_fields_of_their_own():
    """`{}` is the complete default of every REQUIRED singular message.

    Fails when a REQUIRED message gains a REQUIRED field, because an injected
    empty object would then be missing it.
    """
    incomplete = []
    for descriptor in _reachable_messages(AgentCard.DESCRIPTOR).values():
        for field in descriptor.fields:
            message_type = field.message_type
            if (
                not signing._is_required(field)
                or message_type is None
                or signing._is_map(field)
                or signing._field_is_repeated(field)
            ):
                continue
            if any(signing._is_required(f) for f in message_type.fields):
                incomplete.append(f'{descriptor.name}.{field.name}')
    assert incomplete == []


def test_signature_over_spec_canonical_bytes_verifies():
    """A card signed over the section 8.4.1 form of its payload verifies.

    The signing input is built by hand, not by this module, which is how a
    signer in another SDK that keeps REQUIRED fields at their default value
    produces it.
    """
    card = _example_card(description='', skills=[])
    payload = (
        '{"capabilities":{"pushNotifications":false,"streaming":false},'
        '"defaultInputModes":["text/plain"],"defaultOutputModes":["text/plain"],'
        '"description":"","name":"Example Agent","skills":[],'
        '"supportedInterfaces":[{"protocolBinding":"JSONRPC",'
        '"protocolVersion":"1.0","url":"https://example.com/a2a/v1"}],'
        '"version":"1.0.0"}'
    )
    key = 'key12345'
    token = api_jws.encode(
        payload.encode('utf-8'),
        key,
        algorithm='HS384',
        headers={'kid': 'key1'},
    )
    protected, _, signature = token.split('.')
    card.signatures.append(
        AgentCardSignature(protected=protected, signature=signature)
    )
    verifier = signing.create_signature_verifier(
        create_key_provider(key), ['HS384']
    )
    verifier(card)
