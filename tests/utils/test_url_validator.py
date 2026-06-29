# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Tests for a2a.utils.url_validator."""

import ipaddress

import pytest

from a2a.utils.url_validator import (
    BlockPrivateNetworks,
    InvalidUrlError,
    RequireScheme,
    ResolvedUrl,
    UrlValidationRule,
    UrlValidator,
)


class RecordingRule(UrlValidationRule):
    """Records the resolved URL passed to the rule."""

    def __init__(self) -> None:
        self.seen: list[ResolvedUrl] = []

    async def check(self, url: ResolvedUrl) -> None:
        self.seen.append(url)


# ---------------------------------------------------------------------------
# UrlValidator — basic validation & resolution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validate_resolves_and_returns_pinned_addresses() -> None:
    """UrlValidator returns parsed URL details and resolved addresses."""

    def resolver(host: str, port: int | None) -> list[str]:
        assert host == 'example.com'
        assert port == 443
        return ['93.184.216.34', '93.184.216.34']

    rule = RecordingRule()
    validator = UrlValidator(
        [RequireScheme(['https']), rule],
        resolver=resolver,
    )

    result = await validator.validate('https://example.com:443/agent')

    assert result.raw == 'https://example.com:443/agent'
    assert result.parsed.scheme == 'https'
    assert result.parsed.hostname == 'example.com'
    assert result.addresses == (ipaddress.ip_address('93.184.216.34'),)
    assert rule.seen == [result]


@pytest.mark.asyncio
async def test_validate_deduplicates_resolved_addresses() -> None:
    """Duplicate addresses from the resolver are deduplicated."""

    def resolver(host: str, port: int | None) -> list[str]:
        return ['93.184.216.34', '93.184.216.34', '93.184.216.34']

    validator = UrlValidator(resolver=resolver)

    result = await validator.validate('http://example.com/')

    assert result.addresses == (ipaddress.ip_address('93.184.216.34'),)


@pytest.mark.asyncio
async def test_validate_accepts_ip_address_host() -> None:
    """Literal IP addresses in the host are resolved without DNS."""

    validator = UrlValidator([RequireScheme(['http'])])

    result = await validator.validate('http://93.184.216.34/agent')

    assert result.addresses == (ipaddress.ip_address('93.184.216.34'),)


@pytest.mark.asyncio
async def test_validate_accepts_ipv6_host() -> None:
    """IPv6 literal addresses are resolved correctly."""

    validator = UrlValidator([RequireScheme(['http'])])

    result = await validator.validate('http://[::1]/agent')

    assert result.addresses == (ipaddress.ip_address('::1'),)


@pytest.mark.asyncio
async def test_validate_rejects_url_without_host_when_resolving() -> None:
    """URL resolution requires a host."""

    validator = UrlValidator([RequireScheme(['https'])])

    with pytest.raises(InvalidUrlError, match='does not include a host'):
        await validator.validate('https:///missing-host')


@pytest.mark.asyncio
async def test_validate_rejects_invalid_url() -> None:
    """Malformed URLs are reported as InvalidUrlError."""

    validator = UrlValidator(resolve=False)

    with pytest.raises(InvalidUrlError, match='Invalid URL'):
        await validator.validate('http://example.com:not-a-port')


@pytest.mark.asyncio
async def test_validate_reports_resolution_failures() -> None:
    """Resolver failures are reported as InvalidUrlError."""

    def resolver(host: str, port: int | None) -> list[str]:
        raise OSError('name lookup failed')

    validator = UrlValidator(resolver=resolver)

    with pytest.raises(InvalidUrlError, match='Could not resolve URL host'):
        await validator.validate('http://example.com/callback')


@pytest.mark.asyncio
async def test_validate_rejects_empty_resolution_result() -> None:
    """Resolvers must return at least one address."""

    def resolver(host: str, port: int | None) -> list[str]:
        return []

    validator = UrlValidator(resolver=resolver)

    with pytest.raises(InvalidUrlError, match='did not resolve'):
        await validator.validate('http://example.com/callback')


@pytest.mark.asyncio
async def test_validate_without_resolution_runs_rules_with_empty_addresses() -> (
    None
):
    """UrlValidator can skip DNS resolution for parse-only validation."""
    rule = RecordingRule()
    validator = UrlValidator([rule], resolve=False)

    result = await validator.validate('custom://agent/path')

    assert result.parsed.scheme == 'custom'
    assert result.addresses == ()
    assert rule.seen == [result]


@pytest.mark.asyncio
async def test_validate_no_rules_passes() -> None:
    """A validator with no rules always succeeds."""

    def resolver(host: str, port: int | None) -> list[str]:
        return ['93.184.216.34']

    validator = UrlValidator(resolver=resolver)

    result = await validator.validate('http://example.com/')

    assert result.parsed.hostname == 'example.com'


# ---------------------------------------------------------------------------
# RequireScheme
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_require_scheme_rejects_disallowed_scheme() -> None:
    """RequireScheme rejects URLs whose scheme is not allowed."""
    validator = UrlValidator(
        [RequireScheme(['https'])],
        resolve=False,
    )

    with pytest.raises(InvalidUrlError, match='not allowed'):
        await validator.validate('http://example.com')


@pytest.mark.asyncio
async def test_require_scheme_allows_configured_scheme() -> None:
    """RequireScheme allows URLs whose scheme is in the allowed set."""
    validator = UrlValidator(
        [RequireScheme(['http', 'https'])],
        resolve=False,
    )

    result = await validator.validate('http://example.com')

    assert result.parsed.scheme == 'http'


def test_require_scheme_rejects_empty_allowed_schemes() -> None:
    """RequireScheme needs at least one allowed scheme."""
    with pytest.raises(ValueError, match='must not be empty'):
        RequireScheme([])


@pytest.mark.asyncio
async def test_require_scheme_is_case_insensitive() -> None:
    """Scheme comparison is case-insensitive."""
    validator = UrlValidator(
        [RequireScheme(['HTTPS'])],
        resolve=False,
    )

    result = await validator.validate('https://example.com')

    assert result.parsed.scheme == 'https'


# ---------------------------------------------------------------------------
# BlockPrivateNetworks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_block_private_networks_rejects_loopback_address() -> None:
    """BlockPrivateNetworks rejects non-public resolved addresses."""
    validator = UrlValidator([BlockPrivateNetworks()])

    with pytest.raises(InvalidUrlError, match='non-public address 127.0.0.1'):
        await validator.validate('http://127.0.0.1/callback')


@pytest.mark.asyncio
async def test_block_private_networks_rejects_private_10_network() -> None:
    """BlockPrivateNetworks rejects 10.x.x.x addresses."""
    validator = UrlValidator([BlockPrivateNetworks()])

    with pytest.raises(InvalidUrlError, match='non-public address'):
        await validator.validate('http://10.0.0.1/callback')


@pytest.mark.asyncio
async def test_block_private_networks_rejects_private_192_network() -> None:
    """BlockPrivateNetworks rejects 192.168.x.x addresses."""
    validator = UrlValidator([BlockPrivateNetworks()])

    with pytest.raises(InvalidUrlError, match='non-public address'):
        await validator.validate('http://192.168.1.1/callback')


@pytest.mark.asyncio
async def test_block_private_networks_rejects_ipv6_loopback() -> None:
    """BlockPrivateNetworks rejects IPv6 loopback."""
    validator = UrlValidator([BlockPrivateNetworks()])

    with pytest.raises(InvalidUrlError, match='non-public address'):
        await validator.validate('http://[::1]/callback')


@pytest.mark.asyncio
async def test_block_private_networks_allows_public_address() -> None:
    """BlockPrivateNetworks allows public IP addresses."""

    def resolver(host: str, port: int | None) -> list[str]:
        return ['93.184.216.34']

    validator = UrlValidator([BlockPrivateNetworks()], resolver=resolver)

    result = await validator.validate('http://example.com/callback')

    assert result.addresses == (ipaddress.ip_address('93.184.216.34'),)


@pytest.mark.asyncio
async def test_block_private_networks_allows_configured_host() -> None:
    """BlockPrivateNetworks allows explicitly configured hosts."""

    def resolver(host: str, port: int | None) -> list[str]:
        return ['127.0.0.1']

    validator = UrlValidator(
        [BlockPrivateNetworks(allow_hosts=['internal.example.test'])],
        resolver=resolver,
    )

    result = await validator.validate('http://internal.example.test/callback')

    assert result.addresses == (ipaddress.ip_address('127.0.0.1'),)


@pytest.mark.asyncio
async def test_block_private_networks_allows_configured_cidr() -> None:
    """BlockPrivateNetworks allows addresses in configured CIDRs."""
    validator = UrlValidator([BlockPrivateNetworks(allow_cidrs=['10.0.0.0/8'])])

    result = await validator.validate('http://10.1.2.3/callback')

    assert result.addresses == (ipaddress.ip_address('10.1.2.3'),)


@pytest.mark.asyncio
async def test_block_private_networks_rejects_mixed_disallowed_address() -> (
    None
):
    """All resolved addresses must be public or explicitly allowed."""

    def resolver(host: str, port: int | None) -> list[str]:
        return ['93.184.216.34', '10.1.2.3']

    validator = UrlValidator([BlockPrivateNetworks()], resolver=resolver)

    with pytest.raises(InvalidUrlError, match='10.1.2.3'):
        await validator.validate('http://example.com/callback')


@pytest.mark.asyncio
async def test_block_private_networks_rejects_literal_ip_without_resolve() -> (
    None
):
    """BlockPrivateNetworks still rejects literal private IPs when resolve=False.

    This is the defense-in-depth fix for the review comment on PR #1114:
    even without DNS resolution, ``http://127.0.0.1/`` must be rejected.
    """
    validator = UrlValidator(
        [BlockPrivateNetworks()],
        resolve=False,
    )

    with pytest.raises(InvalidUrlError, match='non-public address 127.0.0.1'):
        await validator.validate('http://127.0.0.1/callback')


@pytest.mark.asyncio
async def test_block_private_networks_allows_hostname_without_resolve() -> None:
    """When resolve=False and host is not a literal IP, the rule passes."""

    validator = UrlValidator(
        [BlockPrivateNetworks()],
        resolve=False,
    )

    # Hostname cannot be parsed as IP → addresses empty → rule passes
    result = await validator.validate('http://internal.example.test/callback')

    assert result.parsed.hostname == 'internal.example.test'


@pytest.mark.asyncio
async def test_block_private_networks_allow_host_is_case_insensitive() -> None:
    """allow_hosts matching is case-insensitive and strips trailing dots."""

    def resolver(host: str, port: int | None) -> list[str]:
        return ['127.0.0.1']

    validator = UrlValidator(
        [BlockPrivateNetworks(allow_hosts=['Internal.Example.TEST.'])],
        resolver=resolver,
    )

    result = await validator.validate('http://internal.example.test/callback')

    assert result.addresses == (ipaddress.ip_address('127.0.0.1'),)


@pytest.mark.asyncio
async def test_block_private_networks_allow_ipv6_with_brackets() -> None:
    """allow_hosts strips surrounding brackets from IPv6 literals."""

    def resolver(host: str, port: int | None) -> list[str]:
        return ['::1']

    validator = UrlValidator(
        [BlockPrivateNetworks(allow_hosts=['[::1]'])],
        resolver=resolver,
    )

    result = await validator.validate('http://[::1]/callback')

    assert result.addresses == (ipaddress.ip_address('::1'),)


# ---------------------------------------------------------------------------
# Resolver returning IPAddress objects (PR #1114 review fix)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolver_returning_ipaddress_objects() -> None:
    """Custom resolvers may return IPAddress objects directly."""

    def resolver(host: str, port: int | None) -> list[ipaddress.IPv4Address]:
        return [ipaddress.ip_address('93.184.216.34')]

    validator = UrlValidator(resolver=resolver)

    result = await validator.validate('http://example.com/')

    assert result.addresses == (ipaddress.ip_address('93.184.216.34'),)


@pytest.mark.asyncio
async def test_async_resolver_is_awaited() -> None:
    """Custom resolvers may return awaitables (e.g. aiodns)."""

    async def resolver(host: str, port: int | None) -> list[str]:
        return ['93.184.216.34']

    validator = UrlValidator(resolver=resolver)

    result = await validator.validate('http://example.com/')

    assert result.addresses == (ipaddress.ip_address('93.184.216.34'),)


@pytest.mark.asyncio
async def test_resolver_returning_invalid_address_raises_invalid_url_error() -> (
    None
):
    """Resolver returning non-IP strings raises InvalidUrlError."""

    def resolver(host: str, port: int | None) -> list[str]:
        return ['not-an-ip-address']

    validator = UrlValidator(resolver=resolver)

    with pytest.raises(InvalidUrlError, match='invalid address'):
        await validator.validate('http://example.com/')


# ---------------------------------------------------------------------------
# Rule composition
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rules_run_in_order() -> None:
    """Rules are executed in the order they are provided."""
    order: list[str] = []

    class FirstRule(UrlValidationRule):
        async def check(self, url: ResolvedUrl) -> None:
            order.append('first')

    class SecondRule(UrlValidationRule):
        async def check(self, url: ResolvedUrl) -> None:
            order.append('second')

    validator = UrlValidator(
        [FirstRule(), SecondRule()],
        resolve=False,
    )

    await validator.validate('http://example.com/')

    assert order == ['first', 'second']


@pytest.mark.asyncio
async def test_first_rejecting_rule_stops_further_checks() -> None:
    """When a rule rejects, subsequent rules are not executed."""
    order: list[str] = []

    class RejectingRule(UrlValidationRule):
        async def check(self, url: ResolvedUrl) -> None:
            order.append('rejecting')
            raise InvalidUrlError('rejected')

    class NeverReachedRule(UrlValidationRule):
        async def check(self, url: ResolvedUrl) -> None:
            order.append('never')

    validator = UrlValidator(
        [RejectingRule(), NeverReachedRule()],
        resolve=False,
    )

    with pytest.raises(InvalidUrlError, match='rejected'):
        await validator.validate('http://example.com/')

    assert order == ['rejecting']
