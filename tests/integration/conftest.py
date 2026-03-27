"""conftest.py for tests/integration/

Patches out SSRF DNS validation so that integration tests can use httpx
TestClient's synthetic 'testserver' hostname in AgentCard.url without
triggering real DNS resolution. The validate_agent_card_url function is
tested directly in tests/utils/test_url_validation.py.

Target: tests/integration/conftest.py
"""

import pytest
from unittest.mock import patch


@pytest.fixture(autouse=True)
def bypass_ssrf_url_validation():
    """Bypass DNS-based SSRF validation for all tests in tests/integration/.

    Integration tests use httpx's TestClient which binds to the synthetic
    'testserver' hostname. This hostname cannot be resolved via DNS.
    SSRF URL validation is exercised by its own dedicated test suite in
    tests/utils/test_url_validation.py.
    """
    with patch('a2a.client.card_resolver.validate_agent_card_url'):
        yield
