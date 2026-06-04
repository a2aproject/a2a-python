"""Tests for a2a.utils.json_utils module."""

import json

from a2a.utils import json_utils


def test_dumps_emits_raw_utf8_for_non_ascii() -> None:
    """Non-ASCII characters must serialize as raw UTF-8, not \\uXXXX escapes."""
    out = json_utils.dumps({'text': '你好'})
    assert '你好' in out
    assert '\\u4f60\\u597d' not in out


def test_dumps_emits_emoji_as_raw_utf8() -> None:
    """Emoji (outside the BMP) must also pass through unescaped."""
    out = json_utils.dumps({'emoji': '🎉'})
    assert '🎉' in out


def test_dumps_round_trips_through_json_loads() -> None:
    """Wrapper output must remain a valid JSON document."""
    payload = {'msg': '你好', 'list': ['a', 'é', '日本語'], 'n': 1}
    assert json.loads(json_utils.dumps(payload)) == payload
