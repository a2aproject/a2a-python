"""RFC 8785 JSON Canonicalization Scheme (JCS).

The Agent Card signature covers a canonical serialization of the card, so the
bytes produced here are the bytes that get signed. `json.dumps` cannot produce
them: it escapes non-ASCII by default, orders keys by code point rather than by
UTF-16 code unit, and formats numbers with `repr` rather than with the
ECMAScript `Number::toString` algorithm.

Number formatting follows ECMA-262 7.1.12.1 as amended by RFC 8785 section
3.2.2.3. That part of the algorithm is adapted from Anders Rundgren's reference
implementation, which is published under the Apache License 2.0:
https://github.com/cyberphone/json-canonicalization

Serialization is depth-limited. Nesting is attacker-controlled through
`AgentExtension.params`, which is a `google.protobuf.Struct` and so may nest
arbitrarily, and an unbounded recursive serializer turns that into a crash in
whatever process verifies the card.
"""

from __future__ import annotations

import math
import re

from typing import Any


MAX_DEPTH = 128
"""Maximum object/array nesting accepted by `canonicalize`.

Deep enough that no plausible Agent Card reaches it, shallow enough that the
recursion cannot exhaust the interpreter stack.
"""

# JSON numbers are IEEE 754 double-precision floats (RFC 8785 section 3.2.2.3),
# so integers outside this range have no interoperable representation.
_SAFE_INT_MAX = 2**53 - 1
_SAFE_INT_MIN = -(2**53) + 1

# ECMA-262 7.1.12.1 steps 6 and 8: outside this exponent window a number is
# written in exponential notation, inside it the digits are written out in full.
_EXPONENT_EXPANSION_CEILING = 21
_EXPONENT_EXPANSION_FLOOR = -7

# RFC 8785 section 3.2.2.2: escape the two mandatory characters and the C0
# control range, and emit everything else as literal UTF-8.
_ESCAPE_RE = re.compile(r'[\x00-\x1f\\"]')
_ESCAPE_MAP = {
    '\\': '\\\\',
    '"': '\\"',
    '\b': '\\b',
    '\f': '\\f',
    '\n': '\\n',
    '\r': '\\r',
    '\t': '\\t',
}
for _codepoint in range(0x20):
    _ESCAPE_MAP.setdefault(chr(_codepoint), f'\\u{_codepoint:04x}')
del _codepoint


class CanonicalizationError(ValueError):
    """Raised when a value has no RFC 8785 canonical form."""


def canonicalize(obj: Any) -> str:
    """Serializes `obj` to its RFC 8785 canonical form.

    Args:
        obj: A JSON value: `None`, `bool`, `int`, `float`, `str`, or a list or
            dict of the same. Dict keys must be strings.

    Returns:
        The canonical serialization. The result is `str` rather than `bytes`
        for the caller's convenience; RFC 8785 defines the canonical form as
        the UTF-8 encoding of this string.

    Raises:
        CanonicalizationError: If `obj` contains a value with no canonical
            form, a non-string key, unpaired surrogates, or nesting deeper
            than `MAX_DEPTH`.
    """
    out: list[str] = []
    _write(obj, out, 0)
    canonical = ''.join(out)
    try:
        # RFC 8785 canonical output is UTF-8. Round-tripping here rejects
        # unpaired surrogates, which have no UTF-8 encoding, rather than
        # deferring the failure to whoever encodes the return value.
        canonical.encode('utf-8')
    except UnicodeEncodeError as e:
        raise CanonicalizationError(
            'value contains text that is not valid Unicode'
        ) from e
    return canonical


def _write(obj: Any, out: list[str], depth: int) -> None:
    """Appends the canonical form of `obj` to `out`."""
    if depth > MAX_DEPTH:
        raise CanonicalizationError(
            f'nesting exceeds the maximum depth of {MAX_DEPTH}'
        )

    if isinstance(obj, (list, tuple)):
        out.append('[')
        for index, element in enumerate(obj):
            if index:
                out.append(',')
            _write(element, out, depth + 1)
        out.append(']')
    elif isinstance(obj, dict):
        out.append('{')
        for index, (key, value) in enumerate(_sorted_items(obj)):
            if index:
                out.append(',')
            out.append(_quote(key))
            out.append(':')
            _write(value, out, depth + 1)
        out.append('}')
    else:
        out.append(_format_scalar(obj))


def _format_scalar(obj: Any) -> str:
    """Returns the canonical form of a JSON scalar."""
    if obj is None:
        return 'null'
    # bool is a subclass of int, so it has to be tested first.
    if isinstance(obj, bool):
        return 'true' if obj else 'false'
    if isinstance(obj, int):
        if obj < _SAFE_INT_MIN or obj > _SAFE_INT_MAX:
            raise CanonicalizationError(
                f'{obj} is outside the range of integers a JSON number can '
                'represent exactly'
            )
        return str(obj)
    if isinstance(obj, float):
        return _format_number(obj)
    if isinstance(obj, str):
        return _quote(obj)
    raise CanonicalizationError(
        f'{type(obj).__name__} has no JSON representation'
    )


def _sorted_items(obj: dict[Any, Any]) -> list[tuple[str, Any]]:
    """Sorts a dict's items by UTF-16 code unit, per RFC 8785 section 3.2.3.

    Comparing big-endian UTF-16 encodings byte by byte is equivalent to
    comparing the code unit sequences numerically, which is what the RFC
    requires and what `sorted(..., key=str)` does not do: code point order
    and code unit order disagree for every key containing a character above
    the BMP.
    """
    try:
        return sorted(obj.items(), key=lambda kv: kv[0].encode('utf-16-be'))
    except AttributeError as e:
        raise CanonicalizationError('object keys must be strings') from e
    except UnicodeEncodeError as e:
        raise CanonicalizationError(
            'object key contains text that is not valid Unicode'
        ) from e


def _quote(value: str) -> str:
    """Returns the canonical form of a JSON string."""
    return '"' + _ESCAPE_RE.sub(lambda m: _ESCAPE_MAP[m.group(0)], value) + '"'


def _format_number(value: float) -> str:
    """Formats a float per ECMA-262 7.1.12.1, as RFC 8785 section 3.2.2.3 requires."""
    if math.isnan(value) or math.isinf(value):
        raise CanonicalizationError(f'{value} is not a JSON number')

    # Covers -0.0, which ECMAScript renders as "0".
    if value == 0:
        return '0'

    if value < 0:
        return '-' + _format_number(-value)

    # Adapted from the reference implementation; see the module docstring.
    stringified = str(value)

    exponent_str = ''
    exponent_value = 0
    separator = stringified.find('e')
    if separator > 0:
        exponent_str = stringified[separator:]
        if exponent_str[2:3] == '0':
            # Python pads the exponent to two digits; ECMAScript does not.
            exponent_str = exponent_str[:2] + exponent_str[3:]
        stringified = stringified[0:separator]
        exponent_value = int(exponent_str[1:])

    first = stringified
    dot = ''
    last = ''
    separator = stringified.find('.')
    if separator > 0:
        dot = '.'
        first = stringified[:separator]
        last = stringified[separator + 1 :]

    if last == '0':
        # Python writes an integral float as "1.0"; ECMAScript writes "1".
        dot = ''
        last = ''

    if 0 < exponent_value < _EXPONENT_EXPANSION_CEILING:
        # Values up to 1e21 are written out in full rather than in exponential
        # notation.
        first += last
        last = ''
        dot = ''
        exponent_str = ''
        first += '0' * (exponent_value - len(first) + 1)
    elif _EXPONENT_EXPANSION_FLOOR < exponent_value < 0:
        # Values down to 1e-7 are written as 0.000... rather than exponentially.
        last = first + last
        first = '0'
        dot = '.'
        exponent_str = ''
        last = '0' * (-exponent_value - 1) + last

    return f'{first}{dot}{last}{exponent_str}'
