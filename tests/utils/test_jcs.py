"""RFC 8785 (JCS) conformance tests for Agent Card canonicalization.

The vector corpus in `jcs_vectors.json` is language-neutral and was produced by
two independent RFC 8785 implementations written by neither SDK author, so
these tests check conformance against the RFC rather than against this SDK's
own output.
"""

import json
import math
import random
import struct

from pathlib import Path
from typing import Any

import jwt
import pytest
import rfc8785

from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentExtension,
    AgentInterface,
    AgentSkill,
)
from a2a.utils import signing
from a2a.utils._jcs import MAX_DEPTH, CanonicalizationError, canonicalize
from google.protobuf import struct_pb2


_VECTORS = json.loads(
    (Path(__file__).parent / 'jcs_vectors.json').read_text(encoding='utf-8')
)
_ACCEPT = [v for v in _VECTORS['vectors'] if v['disposition'] == 'MUST-ACCEPT']
_REJECT = [v for v in _VECTORS['vectors'] if v['disposition'] == 'MUST-REJECT']


def _agent_card(
    name: str = 'Agent',
    description: str = 'description',
    ext_params: dict[str, Any] | None = None,
) -> AgentCard:
    """Builds a minimal valid AgentCard, optionally with extension params."""
    capabilities = AgentCapabilities(streaming=True)
    if ext_params is not None:
        params = struct_pb2.Struct()
        params.update(ext_params)
        capabilities.extensions.append(
            AgentExtension(uri='https://example.com/ext', params=params)
        )
    return AgentCard(
        name=name,
        description=description,
        version='1.0.0',
        supported_interfaces=[
            AgentInterface(
                url='https://example.com/a2a/v1',
                protocol_binding='JSONRPC',
                protocol_version='1.0',
            )
        ],
        capabilities=capabilities,
        default_input_modes=['text/plain'],
        default_output_modes=['text/plain'],
        skills=[
            AgentSkill(
                id='skill', name='skill', description='skill', tags=['tag']
            )
        ],
    )


def test_vector_corpus_is_complete():
    """The corpus must be whole; a partially loaded corpus passes vacuously."""
    assert len(_ACCEPT) == _VECTORS['counts']['accept'] == 47
    assert len(_REJECT) == _VECTORS['counts']['reject'] == 10
    assert len(_VECTORS['vectors']) == _VECTORS['counts']['total'] == 57


@pytest.mark.parametrize('vector', _ACCEPT, ids=lambda v: v['id'])
def test_vector_must_accept(vector):
    """Canonical output must match the corpus byte for byte."""
    value = vector['input']
    if vector['group'] == 'a2-signatures-exclusion':
        # Spec 8.4.1 rule 3: the signatures key is excluded unconditionally.
        # This mirrors the two steps _canonicalize_agent_card applies to the
        # card dict before serializing it.
        value = dict(value)
        value.pop('signatures', None)
        value = signing._clean_empty(value)

    actual = canonicalize(value).encode('utf-8')
    assert actual.hex() == vector['canonical_utf8_hex'], vector['rationale']


@pytest.mark.parametrize('vector', _REJECT, ids=lambda v: v['id'])
def test_vector_must_reject(vector):
    """Values with no canonical form must be refused, not silently mangled."""
    if vector['group'] == 'a2-signatures-exclusion':
        # These two vectors reject claimed-canonical output that still carries
        # a signatures key, which is a property of the producer rather than an
        # input it can be handed. The producer side is asserted directly.
        card = _agent_card()
        card.signatures.append(
            signing.AgentCardSignature(protected='abc', signature='def')
        )
        assert 'signatures' not in json.loads(
            signing._canonicalize_agent_card(card)
        )
        return

    value = json.loads(vector['input_raw'])
    with pytest.raises(CanonicalizationError):
        canonicalize(value)


@pytest.mark.parametrize('vector', _ACCEPT, ids=lambda v: v['id'])
def test_vector_agrees_with_independent_implementation(vector):
    """An independent RFC 8785 implementation must produce the same bytes.

    The corpus and the oracle are separate sources of truth: the corpus could
    be transcribed wrongly, and the oracle could be wrong about a clause the
    corpus covers. Requiring both makes a single mistake visible.
    """
    value = vector['input']
    if vector['group'] == 'a2-signatures-exclusion':
        value = dict(value)
        value.pop('signatures', None)
        value = signing._clean_empty(value)
    assert canonicalize(value).encode('utf-8') == rfc8785.dumps(value)


# --- Axis 1: literal UTF-8 rather than \uXXXX escapes (RFC 8785 3.2.2.2) ---


def test_non_ascii_is_emitted_as_literal_utf8():
    """json.dumps defaults to ensure_ascii=True; RFC 8785 forbids the escape."""
    canonical = signing._canonicalize_agent_card(
        _agent_card(name='Café Agent', description='Planifie des itinéraires.')
    )
    assert 'Café Agent' in canonical
    assert '\\u00e9' not in canonical


def test_line_and_paragraph_separators_are_not_escaped():
    """U+2028 and U+2029 are escaped in JavaScript source, not in JCS."""
    canonical = signing._canonicalize_agent_card(_agent_card(name='a b c'))
    assert 'a b c' in canonical
    assert '\\u2028' not in canonical
    assert '\\u2029' not in canonical


def test_mandatory_escapes_are_still_applied():
    """The C0 range, quote and backslash stay escaped (RFC 8785 3.2.2.2)."""
    assert canonicalize({'k': 'a"b\\c\nd\te\x00f\x1ff'}) == (
        '{"k":"a\\"b\\\\c\\nd\\te\\u0000f\\u001ff"}'
    )


def test_non_bmp_characters_survive_as_literal_utf8():
    """Astral characters are one UTF-8 sequence, not two escaped surrogates."""
    canonical = canonicalize({'k': '\U0001f600'})
    assert canonical == '{"k":"\U0001f600"}'
    assert canonical.encode('utf-8') == b'{"k":"\xf0\x9f\x98\x80"}'


# --- Axis 2: UTF-16 code unit key ordering (RFC 8785 3.2.3) ---


def test_keys_sort_by_utf16_code_unit_not_code_point():
    """The two orders disagree whenever a non-BMP key meets a key >= U+E000.

    U+1F600's leading surrogate is 0xD83D, which sorts below U+FF01, while its
    code point U+1F600 sorts above it. sort_keys=True gets this backwards.
    """
    canonical = canonicalize({'\U0001f600': 1, '！': 2})
    assert canonical == '{"\U0001f600":1,"！":2}'
    assert json.dumps(
        {'\U0001f600': 1, '！': 2}, sort_keys=True, ensure_ascii=False
    ) != canonical.replace(':', ': ').replace(',', ', ')
    # The decisive assertion: our first key is the astral one.
    assert list(json.loads(canonical))[0] == '\U0001f600'


def test_key_ordering_reaches_the_canonicalizer_through_extension_params():
    """Arbitrary keys are attacker-reachable via AgentExtension.params."""
    canonical = signing._canonicalize_agent_card(
        _agent_card(ext_params={'\U0001f600': 1, '！': 2})
    )
    params = canonical[canonical.index('"params"') :]
    assert params.index('\U0001f600') < params.index('！')


def test_keys_sort_by_code_unit_across_the_bmp_boundary():
    """A shorter key that is a prefix of a longer one sorts first."""
    assert canonicalize({'ab': 1, 'a': 2, 'b': 3}) == '{"a":2,"ab":1,"b":3}'


def test_empty_key_sorts_first():
    assert canonicalize({'a': 1, '': 2}) == '{"":2,"a":1}'


# --- Axis 3: ECMAScript number formatting (RFC 8785 3.2.2.3) ---


@pytest.mark.parametrize(
    ('value', 'expected'),
    [
        (0.000001, '0.000001'),
        (1e-7, '1e-7'),
        (1e-6, '0.000001'),
        (0.0, '0'),
        (-0.0, '0'),
        (1.0, '1'),
        (-1.0, '-1'),
        (1e20, '100000000000000000000'),
        (1e21, '1e+21'),
        (1.2e21, '1.2e+21'),
        (5e-324, '5e-324'),
        (2.2250738585072014e-308, '2.2250738585072014e-308'),
        (9007199254740991.0, '9007199254740991'),
        (0.1, '0.1'),
        (1.5, '1.5'),
        (1e100, '1e+100'),
    ],
)
def test_number_formatting(value, expected):
    """repr and Number::toString disagree on all of these."""
    assert canonicalize({'n': value}) == f'{{"n":{expected}}}'


def test_number_formatting_matches_the_oracle_over_random_doubles():
    """Sweep the double bit space, not just the cases someone thought of.

    ECMAScript number formatting is the expensive half of RFC 8785 and the
    half a hand-written table cannot cover: the divergences live at the
    exponential-notation thresholds, in the denormal range and wherever the
    shortest round-tripping representation changes length. The seed is fixed
    so any failure names one reproducible double.
    """
    rng = random.Random(8785)
    compared = 0
    for _ in range(20000):
        (value,) = struct.unpack('<d', struct.pack('<Q', rng.getrandbits(64)))
        if not math.isfinite(value):
            continue
        compared += 1
        assert canonicalize([value]) == rfc8785.dumps([value]).decode(), value
    # A sweep that silently compared nothing would pass; assert it ran.
    assert compared > 15000


def test_number_formatting_matches_the_oracle_at_the_exponent_thresholds():
    """The 1e21 and 1e-7 thresholds are where repr and Number::toString part."""
    values = []
    for exponent in range(-330, 309):
        for mantissa in (
            '1',
            '1.5',
            '9',
            '9.999999999999998',
            '1.0000000000000002',
            '5',
            '3',
        ):
            value = float(f'{mantissa}e{exponent}')
            if math.isfinite(value):
                values.append(value)
    assert len(values) > 4000
    for value in values:
        assert canonicalize([value]) == rfc8785.dumps([value]).decode(), value


def test_number_formatting_matches_independent_implementation():
    """Cross-check every number case against the oracle, not just our table."""
    values = [
        0.000001, 1e-7, 1e-6, 0.0, -0.0, 1.0, -1.0, 1e20, 1e21, 1.2e21,
        5e-324, 2.2250738585072014e-308, 9007199254740991.0, 0.1, 1.5, 1e100,
        -1e-7, 3.141592653589793, 1e-323, 1.7976931348623157e308,
    ]  # fmt: skip
    for value in values:
        assert (
            canonicalize({'n': value}) == rfc8785.dumps({'n': value}).decode()
        )


def test_integral_doubles_lose_their_trailing_zero():
    """protobuf Struct stores every number as a double, so this is the common case.

    A card declaring an integer extension param currently signs "1.0"; RFC 8785
    requires "1", which is what every other implementation produces.
    """
    canonical = signing._canonicalize_agent_card(
        _agent_card(ext_params={'count': 1})
    )
    assert '"count":1}' in canonical
    # Anchored on the key: "1.0" also occurs inside the version string.
    assert '"count":1.0' not in canonical


def test_booleans_are_not_treated_as_integers():
    """bool subclasses int, so an unguarded isinstance check emits 1 and 0."""
    assert canonicalize({'a': True, 'b': False}) == '{"a":true,"b":false}'


@pytest.mark.parametrize('value', [float('nan'), float('inf'), float('-inf')])
def test_non_finite_numbers_are_rejected(value):
    with pytest.raises(CanonicalizationError):
        canonicalize({'n': value})


@pytest.mark.parametrize('value', [2**53, -(2**53), 2**63, 10**30])
def test_integers_outside_the_double_range_are_rejected(value):
    """Beyond 2**53-1 an integer has no exact JSON number, so no canonical form."""
    with pytest.raises(CanonicalizationError):
        canonicalize({'n': value})


@pytest.mark.parametrize('value', [2**53 - 1, -(2**53) + 1, 0, -1, 42])
def test_integers_inside_the_double_range_are_accepted(value):
    assert canonicalize({'n': value}) == f'{{"n":{value}}}'


# --- Depth bound ---


def _nest(depth: int) -> dict[str, Any]:
    value: Any = {'x': 1}
    for _ in range(depth):
        value = {'a': value}
    return value


def test_nesting_at_the_limit_is_accepted():
    """Sit on the boundary, not near it.

    _nest(n) builds n wrappers around a leaf object, so the deepest container
    is at depth n + 1. MAX_DEPTH - 1 wrappers is therefore the last accepted
    shape and MAX_DEPTH wrappers is the first rejected one; asserting both
    pins the limit rather than merely staying below it.
    """
    canonicalize(_nest(MAX_DEPTH - 1))
    with pytest.raises(CanonicalizationError):
        canonicalize(_nest(MAX_DEPTH))


@pytest.mark.parametrize('depth', [MAX_DEPTH + 1, 5000])
def test_nesting_beyond_the_limit_is_rejected(depth):
    """Unbounded recursion here is a crash in whoever verifies the card."""
    with pytest.raises(CanonicalizationError):
        canonicalize(_nest(depth))


@pytest.mark.parametrize('depth', [MAX_DEPTH + 1, 5000])
def test_clean_empty_is_bounded_too(depth):
    """_clean_empty runs first, so an uncapped one makes the cap unreachable."""
    with pytest.raises(CanonicalizationError):
        signing._clean_empty(_nest(depth))


def test_deep_arrays_are_bounded():
    value: Any = [1]
    for _ in range(5000):
        value = [value]
    with pytest.raises(CanonicalizationError):
        canonicalize(value)


# --- Types with no canonical form ---


@pytest.mark.parametrize(
    'value',
    ['\ud800', 'a\udc00b', '\ud800\ud800'],
)
def test_unpaired_surrogates_in_values_are_rejected(value):
    with pytest.raises(CanonicalizationError):
        canonicalize({'k': value})


@pytest.mark.parametrize('key', ['\ud800', 'b\udc00key'])
def test_unpaired_surrogates_in_keys_are_rejected(key):
    with pytest.raises(CanonicalizationError):
        canonicalize({key: 1})


def test_non_string_keys_are_rejected():
    with pytest.raises(CanonicalizationError):
        canonicalize({1: 'a'})


def test_unsupported_types_are_rejected():
    with pytest.raises(CanonicalizationError):
        canonicalize({'k': {1, 2}})


def test_noncharacters_are_permitted():
    """U+FFFE and U+FFFF are valid Unicode; JCS has no rule excluding them."""
    assert canonicalize({'k': '￾￿'}) == '{"k":"￾￿"}'


# --- End to end through the signing API ---


def test_signer_and_verifier_agree_on_a_non_ascii_card():
    """The bytes signed must be the canonical bytes, not a re-serialization.

    Handing a parsed dict to the JWT layer lets PyJWT re-encode it with
    ensure_ascii=True, which passes for an ASCII card and fails for every other
    card. The ASCII case is the control.
    """
    key = 'a-shared-secret-of-sufficient-length-for-hs256'
    signer = signing.create_agent_card_signer(key, {'kid': 'k', 'alg': 'HS256'})
    verifier = signing.create_signature_verifier(
        lambda kid, jku: key, ['HS256']
    )
    for name in ('Agent', 'Café Agent', '🤖 Agent', 'a b'):
        verifier(signer(_agent_card(name=name)))


def test_canonical_form_matches_the_reference_for_a_non_ascii_card():
    canonical = signing._canonicalize_agent_card(
        _agent_card(name='Café Agent', description='Planifie des itinéraires.')
    )
    assert canonical.encode('utf-8') == rfc8785.dumps(json.loads(canonical))


def test_uncanonicalizable_card_fails_as_a_signature_error():
    """A hostile card must not raise a new exception type at callers."""
    key = 'a-shared-secret-of-sufficient-length-for-hs256'
    card = _agent_card()
    card.signatures.append(
        signing.AgentCardSignature(protected='abc', signature='def')
    )
    verifier = signing.create_signature_verifier(
        lambda kid, jku: key, ['HS256']
    )
    deep: Any = {'x': 1}
    for _ in range(MAX_DEPTH + 5):
        deep = {'a': deep}
    params = struct_pb2.Struct()
    # Built directly rather than through the proto parser, whose own recursion
    # limit is lower than MAX_DEPTH and would refuse this first.
    _fill_struct(params, deep)
    card.capabilities.extensions.append(
        AgentExtension(uri='https://example.com/ext')
    )
    card.capabilities.extensions[0].params.CopyFrom(params)
    with pytest.raises(signing.InvalidSignaturesError):
        verifier(card)


def _fill_struct(struct: struct_pb2.Struct, value: dict[str, Any]) -> None:
    """Builds a nested Struct without going through the proto parser."""
    for key, item in value.items():
        if isinstance(item, dict):
            _fill_struct(struct.fields[key].struct_value, item)
        else:
            struct.fields[key].number_value = item


def test_protected_header_is_unchanged_by_the_detached_payload_encoding():
    """Signing the canonical bytes must not disturb the protected header.

    The signer hands pre-serialized bytes to the JWS layer so that PyJWT
    cannot re-serialize the payload with ensure_ascii=True. That swap would be
    a silent compatibility break if it also changed the header, since the
    header is what a verifier reads kid and alg out of.
    """
    key = 'a-shared-secret-of-sufficient-length-for-hs256'
    header = {'kid': 'k', 'alg': 'HS256'}
    signed = signing.create_agent_card_signer(key, header)(_agent_card())
    reference = jwt.encode(
        payload={'a': 1}, key=key, algorithm='HS256', headers=dict(header)
    ).split('.')[0]
    assert signed.signatures[0].protected == reference


def test_non_finite_numbers_cannot_reach_the_canonicalizer_through_a_card():
    """protobuf refuses NaN in a Struct, so the guard in _format_number is depth.

    Recording where the first refusal happens keeps a later protobuf change
    from quietly moving the only rejection of NaN out of the stack.
    """
    params = struct_pb2.Struct()
    params.fields['x'].number_value = float('nan')
    card = _agent_card()
    card.capabilities.extensions.append(
        AgentExtension(uri='https://example.com/ext')
    )
    card.capabilities.extensions[0].params.CopyFrom(params)
    with pytest.raises(Exception) as excinfo:
        signing._canonicalize_agent_card(card)
    assert 'NaN' in str(excinfo.value)
