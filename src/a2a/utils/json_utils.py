"""JSON serialization helpers for the A2A Python SDK."""

import json

from typing import Any


def dumps(obj: Any) -> str:
    r"""Serialize ``obj`` to a JSON-formatted ``str`` with UTF-8 defaults.

    Use this in SSE/streaming code paths where payloads are serialized
    manually before being written to the wire. Unary HTTP responses do
    not need it because Starlette's ``JSONResponse.render`` already calls
    ``json.dumps(content, ensure_ascii=False, ...)`` internally; this
    helper makes the streaming paths behave identically so non-ASCII
    characters (CJK, emoji, etc.) reach clients as raw UTF-8 rather than
    escape sequences.
    """
    return json.dumps(obj, ensure_ascii=False)
