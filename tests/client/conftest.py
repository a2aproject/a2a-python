"""conftest.py for tests/client/

Patches out SSRF DNS validation so that card resolver and transport tests can
use test hostnames (localhost, testserver, example.com) without real DNS
lookups. The validate_agent_card_url function is tested directly in
tests/utils/test_url_validation.py.

Target: tests/client/conftest.py
"""

import pytest
from unittest.mock import patch


@pytest.fixture(autouse=True)
def bypass_ssrf_url_validation():
    """Bypass DNS-based SSRF validation for all tests in tests/client/.

    Tests here mock HTTP transports and use synthetic hostnames that do not
    resolve to real IP addresses. SSRF URL validation is exercised by its own
    dedicated test suite in tests/utils/test_url_validation.py.
    """
    with patch('a2a.client.card_resolver.validate_agent_card_url'):
        yield
