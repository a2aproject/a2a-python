"""Tests for version utility functions."""

import pytest

from a2a.compat.v0_3.versions import is_legacy_version


@pytest.mark.parametrize(
    'version, expected',
    [
        ('0.3', True),
        ('0.3.0', True),
        ('0.9', True),
        ('0.9.9', True),
        ('1.0', False),
        ('1.0.0', False),
        ('1.1', False),
        ('0.2', False),
        ('0.2.9', False),
        (None, False),
        ('', False),
        ('invalid', False),
        ('v0.3', True),
    ],
)
def test_is_legacy_version(version, expected):
    assert is_legacy_version(version) == expected
