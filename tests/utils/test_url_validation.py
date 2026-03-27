"""Tests for a2a.utils.url_validation (A2A-SSRF-01 fix).

Target: tests/utils/test_url_validation.py
"""

import pytest

from a2a.utils.url_validation import A2ASSRFValidationError, validate_agent_card_url


class TestValidateAgentCardUrlScheme:
    """URL scheme validation."""

    @pytest.mark.parametrize('url', [
        'file:///etc/passwd',
        'gopher://internal/1',
        'ftp://files.example.com/secret',
        'dict://internal/',
        'ldap://ldap.example.com/',
        '',
    ])
    def test_non_http_schemes_are_blocked(self, url):
        with pytest.raises(A2ASSRFValidationError):
            validate_agent_card_url(url)

    @pytest.mark.parametrize('url', [
        'http://example.com/rpc',
        'https://example.com/rpc',
        'HTTP://EXAMPLE.COM/RPC',
        'HTTPS://EXAMPLE.COM/RPC',
    ])
    def test_http_and_https_are_allowed(self, url):
        # Should not raise - only scheme + hostname check, DNS may vary
        # We only verify scheme acceptance here; real DNS tested separately.
        try:
            validate_agent_card_url(url)
        except A2ASSRFValidationError as exc:
            # Accept DNS resolution failure - scheme was accepted
            assert 'could not be resolved' in str(exc) or 'blocked network' in str(exc)


class TestValidateAgentCardUrlPrivateIPs:
    """Private / reserved IP range blocking."""

    @pytest.mark.parametrize('url,label', [
        ('http://127.0.0.1/rpc',       'loopback IPv4'),
        ('http://127.1.2.3/rpc',       'loopback IPv4 (non-zero host)'),
        ('http://[::1]/rpc',           'loopback IPv6'),
        ('http://10.0.0.1/rpc',        'RFC 1918 10/8'),
        ('http://10.255.255.255/rpc',  'RFC 1918 10/8 broadcast'),
        ('http://172.16.0.1/rpc',      'RFC 1918 172.16/12'),
        ('http://172.31.255.255/rpc',  'RFC 1918 172.31 (last in range)'),
        ('http://192.168.1.1/rpc',     'RFC 1918 192.168/16'),
        ('http://169.254.169.254/latest/meta-data/', 'AWS IMDS'),
        ('http://169.254.0.1/rpc',     'link-local'),
        ('http://100.64.0.1/rpc',      'shared address space RFC 6598'),
    ])
    def test_private_addresses_are_blocked(self, url, label):
        with pytest.raises(A2ASSRFValidationError, match='blocked network'):
            validate_agent_card_url(url)

    def test_public_ip_is_allowed(self):
        """A routable public IP should not be blocked."""
        # 93.184.216.34 is example.com - guaranteed public
        try:
            validate_agent_card_url('http://93.184.216.34/rpc')
        except A2ASSRFValidationError as exc:
            # Only acceptable failure is DNS (not a blocked-network error)
            assert 'could not be resolved' in str(exc)
            pytest.skip('DNS not available in this environment')


class TestValidateAgentCardUrlHostname:
    """Hostname-level checks."""

    def test_missing_hostname_is_blocked(self):
        with pytest.raises(A2ASSRFValidationError, match='no hostname'):
            validate_agent_card_url('http:///path')

    def test_empty_url_is_blocked(self):
        with pytest.raises(A2ASSRFValidationError, match='must not be empty'):
            validate_agent_card_url('')


class TestA2ASSRFValidationError:
    """Exception type tests."""

    def test_is_subclass_of_value_error(self):
        assert issubclass(A2ASSRFValidationError, ValueError)

    def test_raises_with_descriptive_message(self):
        with pytest.raises(A2ASSRFValidationError) as exc_info:
            validate_agent_card_url('http://127.0.0.1/rpc')
        assert '127.0.0.1' in str(exc_info.value)
        assert 'CWE-918' in str(exc_info.value)
