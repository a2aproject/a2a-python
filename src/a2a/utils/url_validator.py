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

"""Composable URL validation utilities for SSRF protection.

This module provides the foundational URL validation infrastructure
described in issue #1023. It is designed to be composable so that
domain-specific wrappers (e.g. ``AgentCardUrlValidator``,
``PushNotificationUrlValidator``) can be built on top of it.

Key design decisions:

* **Composable rules** — validation logic is split into independent
  ``UrlValidationRule`` implementations that are run in order.  A rule
  raises ``InvalidUrlError`` to reject; returning means "continue".
* **Pinned addresses** — ``UrlValidator.validate`` resolves DNS and
  returns the resolved addresses so callers can pin the connection to
  a specific IP, preventing DNS-rebinding attacks.
* **Configurable strictness** — ``BlockPrivateNetworks`` accepts
  ``allow_hosts`` and ``allow_cidrs`` so deployments that legitimately
  use private networks can opt in.
* **Defense in depth** — ``BlockPrivateNetworks`` also inspects the
  host portion of the URL as a literal IP address when ``resolve=False``
  is used, so that ``http://127.0.0.1/`` is still rejected even
  without DNS resolution.
"""

import asyncio
import contextlib
import ipaddress
import socket

from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from urllib.parse import SplitResult, urlsplit


IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
Resolver = Callable[[str, int | None], Sequence[IPAddress | str]]


class InvalidUrlError(ValueError):
    """Raised when URL validation rejects a URL."""


@dataclass(frozen=True)
class ResolvedUrl:
    """A parsed URL and the resolved addresses used for validation.

    Callers can use ``addresses`` to pin the outbound connection to a
    specific IP, preventing DNS-rebinding attacks.
    """

    raw: str
    parsed: SplitResult
    addresses: tuple[IPAddress, ...]


class UrlValidationRule(ABC):
    """A composable URL validation rule.

    Subclass and implement ``check``.  Raise ``InvalidUrlError`` to
    reject the URL; return normally to allow subsequent rules to run.
    """

    @abstractmethod
    async def check(self, url: ResolvedUrl) -> None:
        """Raise ``InvalidUrlError`` to reject the URL."""


class RequireScheme(UrlValidationRule):
    """Require a URL scheme to be one of the configured schemes.

    Typical usage::

        RequireScheme(['https'])  # HTTPS only
        RequireScheme(['http', 'https'])  # HTTP or HTTPS
    """

    def __init__(self, allowed_schemes: Sequence[str]) -> None:
        if not allowed_schemes:
            raise ValueError('allowed_schemes must not be empty.')
        self._allowed_schemes = frozenset(
            scheme.lower() for scheme in allowed_schemes
        )

    async def check(self, url: ResolvedUrl) -> None:
        """Reject URLs whose scheme is not configured as allowed."""
        scheme = url.parsed.scheme.lower()
        if scheme not in self._allowed_schemes:
            allowed = ', '.join(sorted(self._allowed_schemes))
            raise InvalidUrlError(
                f'URL scheme {url.parsed.scheme!r} is not allowed. '
                f'Allowed schemes: {allowed}.'
            )


class BlockPrivateNetworks(UrlValidationRule):
    """Reject URLs resolving to non-public IP addresses.

    Hosts in ``allow_hosts`` and addresses covered by ``allow_cidrs``
    are exempt from the non-public address check.

    When ``resolve=False`` is configured on the ``UrlValidator``, the
    ``addresses`` tuple will be empty.  In that case this rule still
    inspects the host portion of the URL as a literal IP address so
    that ``http://127.0.0.1/`` is rejected even without DNS resolution.
    """

    def __init__(
        self,
        *,
        allow_hosts: Sequence[str] = (),
        allow_cidrs: Sequence[str] = (),
    ) -> None:
        self._allow_hosts = frozenset(
            _normalize_host(host) for host in allow_hosts
        )
        self._allow_networks = tuple(
            ipaddress.ip_network(cidr, strict=False) for cidr in allow_cidrs
        )

    async def check(self, url: ResolvedUrl) -> None:
        """Reject URLs that resolve to non-public addresses."""
        host = url.parsed.hostname
        if host is not None and _normalize_host(host) in self._allow_hosts:
            return

        # Use resolved addresses when available; fall back to parsing
        # the host as a literal IP for the resolve=False case.
        addresses = url.addresses
        if not addresses and host is not None:
            with contextlib.suppress(ValueError):
                addresses = (ipaddress.ip_address(host),)

        for address in addresses:
            if any(address in network for network in self._allow_networks):
                continue
            if not address.is_global:
                raise InvalidUrlError(
                    f'URL host {host!r} resolves to non-public address '
                    f'{address}.'
                )


class UrlValidator:
    """Validate URLs by parsing, resolving, then running rules in order.

    Example::

        validator = UrlValidator(
            [
                RequireScheme(['https']),
                BlockPrivateNetworks(),
            ]
        )
        resolved = await validator.validate('https://example.com/agent')
        # Use resolved.addresses to pin the connection IP.
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
        self._resolver = resolver

    async def validate(self, url: str) -> ResolvedUrl:
        """Validate a URL and return the parsed URL plus resolved addresses."""
        resolved = await self._build(url)
        for rule in self._rules:
            await rule.check(resolved)
        return resolved

    async def _build(self, url: str) -> ResolvedUrl:
        try:
            parsed = urlsplit(url)
            host = parsed.hostname
            port = parsed.port
        except ValueError as exc:
            raise InvalidUrlError(f'Invalid URL {url!r}: {exc}') from exc

        addresses: tuple[IPAddress, ...] = ()
        if self._resolve:
            if host is None:
                raise InvalidUrlError(f'URL {url!r} does not include a host.')
            addresses = await self._resolve_host(host, port)

        return ResolvedUrl(raw=url, parsed=parsed, addresses=addresses)

    async def _resolve_host(
        self, host: str, port: int | None
    ) -> tuple[IPAddress, ...]:
        # Fast path: host is already a literal IP address.
        try:
            return (ipaddress.ip_address(host),)
        except ValueError:
            pass

        try:
            if self._resolver is not None:
                resolved = self._resolver(host, port)
            else:
                loop = asyncio.get_running_loop()
                address_info = await loop.getaddrinfo(
                    host,
                    port,
                    type=socket.SOCK_STREAM,
                )
                resolved = [info[4][0] for info in address_info]
        except OSError as exc:
            raise InvalidUrlError(
                f'Could not resolve URL host {host!r}: {exc}'
            ) from exc

        # Normalise resolver output: accept both str and IPAddress
        # instances (fixes review comment on PR #1114).
        addresses = tuple(
            dict.fromkeys(
                addr
                if isinstance(
                    addr, (ipaddress.IPv4Address, ipaddress.IPv6Address)
                )
                else ipaddress.ip_address(addr)
                for addr in resolved
            )
        )
        if not addresses:
            raise InvalidUrlError(f'URL host {host!r} did not resolve.')
        return addresses


def _normalize_host(host: str) -> str:
    return host.rstrip('.').lower()
