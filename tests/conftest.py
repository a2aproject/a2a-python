import pytest
from unittest.mock import patch


@pytest.fixture(autouse=True)
def bypass_ssrf_url_validation(request):
    """Bypass DNS-based SSRF validation for all tests except test_url_validation.

    Most tests use synthetic hostnames (localhost, testserver, example.com)
    that either resolve to loopback or are unavailable in CI. The actual SSRF
    validation logic is tested in tests/utils/test_url_validation.py.
    """
    if "test_url_validation" in request.node.nodeid:
        yield
    else:
        with patch("a2a.client.card_resolver.validate_agent_card_url"):
            yield
