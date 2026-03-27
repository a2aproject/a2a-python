"""URL validation utilities for A2A agent card URLs.

Prevents Server-Side Request Forgery (SSRF) attacks by validating that
AgentCard.url values do not point to private, loopback, or link-local
network addresses before the SDK uses them as RPC endpoints.

Fix for: A2A-SSRF-01 (CWE-918)
Target:  src/a2a/utils/url_validation.py  (new file)
"""

import ipaddress
import logging
import socket
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Only these schemes are permitted in AgentCard.url values.
_ALLOWED_SCHEMES = frozenset({'http', 'https'})

# Networks that must never be reachable via a resolved AgentCard URL.
# Covers: loopback, RFC 1918 private ranges, link-local (IMDS), and other
# IANA-reserved blocks that have no legitimate use as public agent endpoints.
_BLOCKED_NETWORKS: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = (
    # Loopback
    ipaddress.ip_network('127.0.0.0/8'),
    ipaddress.ip_network('::1/128'),
    # RFC 1918 private ranges
    ipaddress.ip_network('10.0.0.0/8'),
    ipaddress.ip_network('172.16.0.0/12'),
    ipaddress.ip_network('192.168.0.0/16'),
    # Link-local -- covers AWS/GCP/Azure/OCI IMDS (169.254.169.254)
    ipaddress.ip_network('169.254.0.0/16'),
    ipaddress.ip_network('fe80::/10'),
    # IPv6 unique local (ULA) -- equivalent of RFC 1918 for IPv6
    ipaddress.ip_network('fc00::/7'),
    # Shared address space (RFC 6598 -- carrier-grade NAT)
    ipaddress.ip_network('100.64.0.0/10'),
    # Other IANA reserved / unroutable
    ipaddress.ip_network('0.0.0.0/8'),
    ipaddress.ip_network('192.0.0.0/24'),
    ipaddress.ip_network('198.18.0.0/15'),
    ipaddress.ip_network('240.0.0.0/4'),
)


class A2ASSRFValidationError(ValueError):
    """Raised when an AgentCard URL fails SSRF validation."""


def validate_agent_card_url(url: str) -> None:
    """Validate that *url* is safe to use as an A2A RPC endpoint.

    Checks performed (in order):

    1. URL must be parseable and non-empty.
    2. Scheme must be ``http`` or ``https``.
    3. Hostname must be present and non-empty.
    4. The hostname must resolve to a publicly routable IP address -- it must
       not resolve to a loopback, private, link-local, or otherwise reserved
       address (SSRF / IMDS protection).

    Args:
        url: The URL string from ``AgentCard.url`` (or
            ``AgentInterface.url``) to validate.

    Raises:
        A2ASSRFValidationError: If the URL fails any validation check.
    """
    if not url:
        raise A2ASSRFValidationError('AgentCard URL must not be empty.')

    parsed = urlparse(url)

    # 1. Scheme check
    scheme = (parsed.scheme or '').lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise A2ASSRFValidationError(
            f'AgentCard URL scheme {scheme!r} is not permitted. '
            f'Allowed schemes: {sorted(_ALLOWED_SCHEMES)}. '
            'Arbitrary schemes allow SSRF attacks (CWE-918).'
        )

    # 2. Hostname presence
    hostname = parsed.hostname
    if not hostname:
        raise A2ASSRFValidationError(
            f'AgentCard URL {url!r} contains no hostname.'
        )

    # 3. Resolve hostname and check against blocked networks
    try:
        # getaddrinfo returns all A/AAAA records; check every resolved address.
        addr_infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        raise A2ASSRFValidationError(
            f'AgentCard URL hostname {hostname!r} could not be resolved: {exc}. '
            'Unresolvable hostnames may indicate DNS rebinding attempts.'
        ) from exc

    for _family, _type, _proto, _canonname, sockaddr in addr_infos:
        ip_str = sockaddr[0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue

        for blocked in _BLOCKED_NETWORKS:
            if ip in blocked:
                raise A2ASSRFValidationError(
                    f'AgentCard URL {url!r} resolves to {ip_str}, '
                    f'which is within the blocked network {blocked}. '
                    'Requests to private/loopback/link-local addresses are '
                    'forbidden to prevent SSRF attacks (CWE-918).'
                )

    logger.debug('AgentCard URL passed SSRF validation: %s', url)
