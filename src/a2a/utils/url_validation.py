"""Composable, domain-agnostic URL validation.

Foundation for screening URLs that the SDK fetches or dials (agent card
endpoints, push-notification webhooks) before invocation, so that
user-controlled URLs cannot reach loopback, private, or otherwise
non-public destinations (SSRF).

The design follows the sketch agreed on issue #1023: a core
:class:`UrlValidator` parses and (optionally) resolves the URL, runs a
sequence of composable :class:`UrlValidationRule` objects, and returns
the resolved URL — including the addresses the host resolved to, so
callers can pin the connection to a validated address. Domain-specific
wrappers (e.g.
:func:`a2a.utils.push_url_validator.validate_push_notification_url`)
own which fields to validate and how failures surface.
"""

import asyncio
import ipaddress
import logging
import socket
import urllib.parse

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass


logger = logging.getLogger(__name__)

_Address = ipaddress.IPv4Address | ipaddress.IPv6Address

Resolver = Callable[[str, int], Awaitable[list[tuple]]]


class InvalidUrlError(ValueError):
    """Raised by UrlValidator.validate when a URL is rejected."""


@dataclass(frozen=True)
class ResolvedUrl:
    """A parsed URL together with the addresses its host resolved to."""

    raw: str
    parsed: urllib.parse.SplitResult
    addresses: tuple[_Address, ...]

    @property
    def hostname(self) -> str:
        """The lowercased host from the parsed URL (may be empty)."""
        return self.parsed.hostname or ''


def _is_non_public(address: _Address) -> bool:
    """Whether an address is not a public unicast destination."""
    return (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


class UrlValidationRule(ABC):
    """A single composable URL check.

    Implementations raise :class:`InvalidUrlError` to reject the URL;
    returning normally means 'continue to the next rule'.
    """

    @abstractmethod
    async def check(self, url: ResolvedUrl) -> None:
        """Check the resolved URL. Raise InvalidUrlError to reject."""


class RequireScheme(UrlValidationRule):
    """Rejects URLs whose scheme is not one of the allowed values."""

    def __init__(self, schemes: Sequence[str]) -> None:
        self._schemes = tuple(scheme.lower() for scheme in schemes)

    async def check(self, url: ResolvedUrl) -> None:
        """Reject the URL when its scheme is not allowed."""
        if url.parsed.scheme not in self._schemes:
            raise InvalidUrlError(
                f'Scheme {url.parsed.scheme!r} is not allowed; expected one '
                f'of: {", ".join(self._schemes)}'
            )


class BlockPrivateNetworks(UrlValidationRule):
    """Rejects hosts resolving to non-public addresses.

    Blocks loopback, private, link-local, multicast, reserved, and
    unspecified destinations (e.g. 169.254.169.254 cloud metadata,
    internal services). Deployments that legitimately use private
    networks can exempt specific hostnames via ``allow_hosts`` or whole
    ranges via ``allow_cidrs``.
    """

    def __init__(
        self,
        *,
        allow_hosts: Sequence[str] = (),
        allow_cidrs: Sequence[str] = (),
    ) -> None:
        self._allow_hosts = frozenset(allow_hosts)
        self._allow_cidrs = tuple(
            ipaddress.ip_network(cidr, strict=False) for cidr in allow_cidrs
        )

    def _is_allowed(self, url: ResolvedUrl, address: _Address) -> bool:
        if url.hostname in self._allow_hosts:
            return True
        return any(address in network for network in self._allow_cidrs)

    async def check(self, url: ResolvedUrl) -> None:
        """Reject the URL when any resolved address is non-public."""
        for address in url.addresses:
            if self._is_allowed(url, address):
                continue
            if _is_non_public(address):
                raise InvalidUrlError(
                    f'Host {url.hostname!r} resolves to a non-public '
                    f'address: {address}'
                )


async def _default_resolver(host: str, port: int) -> list[tuple]:
    loop = asyncio.get_running_loop()
    return await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)


class UrlValidator:
    """Parses and optionally resolves a URL, then runs composable rules.

    Rules run in order; the first rule that raises :class:`InvalidUrlError`
    rejects the URL. With ``resolve=True`` (default) the host is resolved
    via the running event loop's ``getaddrinfo`` before rules run, so
    IP-based rules (e.g. :class:`BlockPrivateNetworks`) see every address
    the host maps to — and resolution failures fail closed.
    """

    def __init__(
        self,
        rules: Sequence[UrlValidationRule] = (),
        *,
        resolve: bool = True,
        resolver: Resolver | None = None,
    ) -> None:
        self._rules = tuple(rules)
        self._resolve = resolve
        self._resolver = resolver or _default_resolver

    async def validate(self, url: str) -> ResolvedUrl:
        """Validate ``url`` and return it with its resolved addresses."""
        try:
            parsed = urllib.parse.urlsplit(url)
            port = parsed.port or (443 if parsed.scheme == 'https' else 80)
        except ValueError as e:
            raise InvalidUrlError(f'URL is unparseable: {url}') from e
        host = parsed.hostname
        if not host:
            raise InvalidUrlError(f'URL has no hostname: {url}')

        addresses: tuple[_Address, ...] = ()
        if self._resolve:
            try:
                infos = await self._resolver(host, port)
            except OSError as e:
                raise InvalidUrlError(
                    f'Host {host!r} could not be resolved: {url}'
                ) from e
            resolved: list[_Address] = []
            for info in infos:
                raw_ip = str(info[4][0]).split('%', maxsplit=1)[0]
                try:
                    resolved.append(ipaddress.ip_address(raw_ip))
                except ValueError as e:
                    raise InvalidUrlError(
                        f'Host {host!r} resolved to an unparseable address'
                    ) from e
            addresses = tuple(resolved)

        resolved_url = ResolvedUrl(raw=url, parsed=parsed, addresses=addresses)
        for rule in self._rules:
            await rule.check(resolved_url)
        return resolved_url


class PushNotificationUrlValidator:
    """Domain wrapper validating push-notification webhook URLs.

    Keeps the boolean, fail-closed contract of
    :func:`a2a.utils.push_url_validator.validate_push_notification_url`.
    """

    def __init__(self) -> None:
        self._validator = UrlValidator(
            rules=(RequireScheme(('http', 'https')), BlockPrivateNetworks())
        )

    async def validate(self, url: str) -> bool:
        """Return True if the webhook URL is safe to fetch."""
        try:
            await self._validator.validate(url)
        except InvalidUrlError as e:
            logger.warning('Push-notification URL rejected: %s (%s)', url, e)
            return False
        return True


class AgentCardUrlValidator:
    """Domain wrapper validating URLs advertised on an agent card.

    Covers the service endpoint and additional interfaces, screening
    them before the SDK dials them. By default only http/https on
    public addresses are allowed; deployments that legitimately target
    private networks can pass ``allow_private_networks=True``.
    """

    def __init__(
        self,
        *,
        allowed_schemes: Sequence[str] = ('http', 'https'),
        allow_private_networks: bool = False,
        resolver: Resolver | None = None,
    ) -> None:
        rules: list[UrlValidationRule] = [RequireScheme(allowed_schemes)]
        if not allow_private_networks:
            rules.append(BlockPrivateNetworks())
        self._validator = UrlValidator(rules, resolver=resolver)

    async def validate(self, url: str) -> ResolvedUrl:
        """Validate and return the resolved URL (addresses included)."""
        return await self._validator.validate(url)
