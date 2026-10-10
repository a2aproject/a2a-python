"""Tests for the composable URL validation primitives (issue #1023)."""

import asyncio

import pytest

from a2a.utils.url_validation import (
    AgentCardUrlValidator,
    BlockPrivateNetworks,
    InvalidUrlError,
    RequireScheme,
    UrlValidator,
)


PUBLIC_IP = '93.184.216.34'


def _resolver_with(*ips: str):
    async def resolve(host: str, port: int):
        return [(2, 1, 6, '', (ip, port)) for ip in ips]

    return resolve


def _resolver_failing():
    async def resolve(host: str, port: int):
        raise OSError('no DNS')

    return resolve


async def _validate(
    url: str,
    *,
    rules=None,
    ips: tuple[str, ...] = (PUBLIC_IP,),
    resolve: bool = True,
    resolver=None,
):
    if rules is None:
        rules = (RequireScheme(('http', 'https')), BlockPrivateNetworks())
    if resolver is None:
        resolver = _resolver_with(*ips)
    validator = UrlValidator(rules, resolve=resolve, resolver=resolver)
    return await validator.validate(url)


@pytest.mark.asyncio
async def test_public_http_url_passes_and_returns_resolved_url():
    resolved = await _validate('http://example.com/callback')

    assert resolved.raw == 'http://example.com/callback'
    assert resolved.parsed.hostname == 'example.com'
    assert [str(a) for a in resolved.addresses] == [PUBLIC_IP]


@pytest.mark.asyncio
async def test_resolver_receives_default_port_by_scheme():
    seen_ports = []

    async def recording_resolver(host: str, port: int):
        seen_ports.append(port)
        return [(2, 1, 6, '', (PUBLIC_IP, port))]

    validator = UrlValidator(
        (RequireScheme(('http', 'https')),), resolver=recording_resolver
    )
    await validator.validate('https://example.com/hook')
    await validator.validate('http://example.com/hook')

    assert seen_ports == [443, 80]


@pytest.mark.asyncio
async def test_resolver_receives_explicit_port():
    seen_ports = []

    async def recording_resolver(host: str, port: int):
        seen_ports.append(port)
        return [(2, 1, 6, '', (PUBLIC_IP, port))]

    validator = UrlValidator(
        (RequireScheme(('http', 'https')),), resolver=recording_resolver
    )
    await validator.validate('http://example.com:8080/hook')

    assert seen_ports == [8080]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'ip',
    [
        '127.0.0.1',
        '10.0.0.5',
        '172.16.1.9',
        '192.168.0.20',
        '169.254.169.254',
        '224.0.0.1',
        '240.0.0.1',
        '0.0.0.0',
    ],
)
async def test_block_private_networks_rejects_non_public_ips(ip: str):
    with pytest.raises(InvalidUrlError, match='non-public'):
        await _validate(f'http://example.com/hook', ips=(ip,))


@pytest.mark.asyncio
async def test_block_private_networks_allow_hosts_exception():
    rules = (
        RequireScheme(('http', 'https')),
        BlockPrivateNetworks(allow_hosts=('internal.corp',)),
    )
    resolved = await _validate(
        'http://internal.corp/api', rules=rules, ips=('10.1.2.3',)
    )

    assert resolved.hostname == 'internal.corp'


@pytest.mark.asyncio
async def test_block_private_networks_allow_cidrs_exception():
    rules = (
        RequireScheme(('http', 'https')),
        BlockPrivateNetworks(allow_cidrs=('10.1.2.0/24',)),
    )
    resolved = await _validate(
        'http://internal.corp/api', rules=rules, ips=('10.1.2.3',)
    )

    assert resolved.hostname == 'internal.corp'


@pytest.mark.asyncio
async def test_block_private_networks_allow_cidrs_still_blocks_outside():
    rules = (
        RequireScheme(('http', 'https')),
        BlockPrivateNetworks(allow_cidrs=('10.1.2.0/24',)),
    )
    with pytest.raises(InvalidUrlError, match='non-public'):
        await _validate(
            'http://internal.corp/api', rules=rules, ips=('10.9.9.9',)
        )


@pytest.mark.asyncio
async def test_block_private_networks_ignores_public_addresses():
    resolved = await _validate('http://example.com/hook')

    assert resolved.hostname == 'example.com'


@pytest.mark.asyncio
async def test_require_scheme_rejects_other_schemes():
    with pytest.raises(InvalidUrlError, match="[Ss]cheme 'ftp'"):
        await _validate('ftp://example.com/file')


@pytest.mark.asyncio
async def test_require_scheme_is_case_insensitive():
    resolved = await _validate('HTTP://example.com/hook')

    assert resolved.parsed.scheme == 'http'


@pytest.mark.asyncio
async def test_missing_scheme_is_rejected():
    with pytest.raises(InvalidUrlError):
        await _validate('example.com/hook')


@pytest.mark.asyncio
async def test_unresolvable_host_fails_closed():
    with pytest.raises(InvalidUrlError, match='could not be resolved'):
        await _validate(
            'http://does-not-resolve.invalid/',
            resolver=_resolver_failing(),
        )


@pytest.mark.asyncio
async def test_unparseable_url_is_rejected():
    with pytest.raises(InvalidUrlError, match='unparseable'):
        await _validate('http://[invalid-ipv6/hook')


@pytest.mark.asyncio
async def test_invalid_port_is_rejected():
    with pytest.raises(InvalidUrlError, match='unparseable'):
        await _validate('http://example.com:99999/hook')


@pytest.mark.asyncio
async def test_missing_hostname_is_rejected():
    with pytest.raises(InvalidUrlError, match='no hostname'):
        await _validate('http:///path')


@pytest.mark.asyncio
async def test_resolve_false_skips_ip_rules_but_keeps_scheme_rules():
    rules = (RequireScheme(('http', 'https')), BlockPrivateNetworks())
    resolved = await _validate(
        'http://internal.corp/api',
        rules=rules,
        ips=('10.9.9.9',),
        resolve=False,
    )

    assert resolved.addresses == ()
    assert resolved.hostname == 'internal.corp'


@pytest.mark.asyncio
async def test_rules_run_in_order_and_first_rejection_wins():
    order = []

    class RecordingRule(RequireScheme):
        async def check(self, url):
            order.append(self.__class__.__name__)
            await super().check(url)

    class SecondRule(BlockPrivateNetworks):
        async def check(self, url):
            order.append('SecondRule')
            await super().check(url)

    validator = UrlValidator(
        (
            RecordingRule(('http', 'https')),
            SecondRule(),
        ),
        resolver=_resolver_with('127.0.0.1'),
    )
    with pytest.raises(InvalidUrlError):
        await validator.validate('http://example.com/hook')

    assert order[0] == 'RecordingRule'


@pytest.mark.asyncio
async def test_agent_card_url_validator_accepts_public_endpoint():
    validator = AgentCardUrlValidator(resolver=_resolver_with(PUBLIC_IP))
    resolved = await validator.validate('https://agent.example.com/card')

    assert resolved.hostname == 'agent.example.com'
    assert [str(a) for a in resolved.addresses] == [PUBLIC_IP]


@pytest.mark.asyncio
async def test_agent_card_url_validator_blocks_private_by_default():
    validator = AgentCardUrlValidator(resolver=_resolver_with('10.0.0.9'))
    with pytest.raises(InvalidUrlError, match='non-public'):
        await validator.validate('http://internal.agent:8080/card')


@pytest.mark.asyncio
async def test_agent_card_url_validator_allows_private_when_opted_in():
    validator = AgentCardUrlValidator(
        allow_private_networks=True, resolver=_resolver_with('10.0.0.9')
    )
    resolved = await validator.validate('http://internal.agent:8080/card')

    assert resolved.hostname == 'internal.agent'


@pytest.mark.asyncio
async def test_agent_card_url_validator_supports_https_only():
    validator = AgentCardUrlValidator(
        allowed_schemes=('https',), resolver=_resolver_with(PUBLIC_IP)
    )
    with pytest.raises(InvalidUrlError, match="[Ss]cheme 'http'"):
        await validator.validate('http://agent.example.com/card')
    resolved = await validator.validate('https://agent.example.com/card')

    assert resolved.hostname == 'agent.example.com'


@pytest.mark.asyncio
async def test_ipv4_mapped_ipv6_loopback_is_rejected():
    with pytest.raises(InvalidUrlError, match='non-public'):
        await _validate('http://example.com/hook', ips=('::ffff:127.0.0.1',))


@pytest.mark.asyncio
async def test_multiple_resolved_addresses_any_non_public_rejects():
    with pytest.raises(InvalidUrlError, match='non-public'):
        await _validate(
            'http://dual-homed.example/', ips=(PUBLIC_IP, '192.168.1.1')
        )


@pytest.mark.asyncio
async def test_validate_can_be_cancelled_while_resolving():
    validator = UrlValidator(
        (RequireScheme(('http', 'https')),),
        resolver=_resolver_with(PUBLIC_IP),
    )

    async def never_returning_resolver(host: str, port: int):
        await asyncio.Event().wait()

    validator._resolver = never_returning_resolver  # type: ignore[assignment]
    task = asyncio.create_task(validator.validate('http://example.com/'))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
