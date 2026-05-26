"""JSON serialization helpers for the A2A Python SDK."""

import json

from typing import Any


def dumps(obj: Any) -> str:
    """Serialize ``obj`` to a JSON-formatted ``str`` with UTF-8 defaults.

    Non-ASCII characters are emitted as raw UTF-8 (``ensure_ascii=False``)
    so the output matches the ``charset=utf-8`` transport used for wire
    responses.
    """
    return json.dumps(obj, ensure_ascii=False)
