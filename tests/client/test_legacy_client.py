"""Tests for the legacy client compatibility layer.

TODO: The A2AClient and A2AGrpcClient classes have been removed in the
proto migration. This test file should be removed or the tests should
be migrated to test the new Client/ClientFactory API.
"""
import pytest

pytestmark = pytest.mark.skip(
    reason="A2AClient/A2AGrpcClient no longer exist - needs migration to new API"
)


def test_placeholder():
    """Placeholder test - legacy classes removed."""
    pass
