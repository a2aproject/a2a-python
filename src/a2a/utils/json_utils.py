"""JSON serialization helpers for the A2A Python SDK."""

import json

from typing import Any


# SSE defines CR, LF and CRLF as its only line endings, but httpx's
# ``aiter_lines()`` follows ``str.splitlines()`` and also breaks on U+0085,
# U+2028 and U+2029. Escaping those three keeps a streamed payload on a
# single ``data:`` line; ``json.loads()`` restores them on the client.
_SSE_LINE_SEPARATOR_ESCAPES = str.maketrans(
    {'\u2028': '\\u2028', '\u2029': '\\u2029', '\u0085': '\\u0085'}
)


def dumps(obj: Any) -> str:
    r"""Serialize ``obj`` to a JSON-formatted ``str`` with UTF-8 defaults.

    Use this in SSE/streaming code paths where payloads are serialized
    manually before being written to the wire. Unary HTTP responses do
    not need it because Starlette's ``JSONResponse.render`` already calls
    ``json.dumps(content, ensure_ascii=False, ...)`` internally; this
    helper makes the streaming paths behave identically so non-ASCII
    characters (CJK, emoji, etc.) reach clients as raw UTF-8 rather than
    escape sequences.

    U+2028, U+2029 and U+0085 are emitted as JSON Unicode escapes instead:
    they are valid JSON string content, but Python's ``str.splitlines()``
    treats them as line breaks and would split an SSE ``data:`` line.
    """
    return json.dumps(obj, ensure_ascii=False).translate(
        _SSE_LINE_SEPARATOR_ESCAPES
    )
