"""Tests for a2a.client.optionals module."""

import importlib
import sys

from unittest.mock import patch


def test_channel_import_failure():
    """Test Channel behavior when grpc is not available."""
    with patch.dict('sys.modules', {'grpc': None, 'grpc.aio': None}):
        if 'a2a.client.optionals' in sys.modules:
            del sys.modules['a2a.client.optionals']

        optionals = importlib.import_module('a2a.client.optionals')
        assert optionals.Channel is None
