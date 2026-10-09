"""Shared policy for screening client-supplied push-notification URLs."""

import asyncio
import ipaddress
import logging
import socket
import urllib.parse


logger = logging.getLogger(__name__)

# RFC 6598. ipaddress leaves is_private false for this range on every
# Python this package supports (3.10 through 3.14), while is_global is
# also false. The flag checks below would accept it.
_SHARED_ADDRESS_SPACE = ipaddress.ip_network('100.64.0.0/10')


def _ip_is_blocked(ip_str: str) -> bool:
    """Whether an address is not a public unicast destination.

    RFC 6598 shared address space (100.64.0.0/10) is not public, but
    ``ipaddress`` leaves ``is_private`` false for it. An IPv4-mapped
    IPv6 address is judged as the IPv4 address inside it.
    """
    try:
        addr = ipaddress.ip_address(ip_str.split('%', maxsplit=1)[0])
    except ValueError:
        return True
    mapped = getattr(addr, 'ipv4_mapped', None)
    if mapped is not None:
        addr = mapped
    if (
        isinstance(addr, ipaddress.IPv4Address)
        and addr in _SHARED_ADDRESS_SPACE
    ):
        return True
    return (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    )


async def validate_push_notification_url(url: str) -> bool:
    """Return True if a push-notification URL is safe to fetch.

    Blocks non-HTTP(S) schemes and hosts that resolve to loopback,
    link-local, private, shared (100.64.0.0/10), reserved, multicast,
    or unspecified addresses (e.g. 169.254.169.254 cloud metadata,
    internal services). A host that cannot be resolved is rejected:
    the POST would fail anyway, and failing closed avoids treating
    resolution errors as a bypass.

    IPv4-mapped IPv6 forms are judged as the IPv4 address they carry,
    so the private, shared, and loopback checks apply to that address.

    Uses the running event-loop resolver so request handlers and the
    sender stay non-blocking. Deployments can pass this function as
    ``push_url_validator`` on ``DefaultRequestHandler`` /
    ``DefaultRequestHandlerV2`` / ``BasePushNotificationSender``.
    The default on those constructors is ``None`` (no library
    screening).
    """
    try:
        parsed = urllib.parse.urlparse(url)
        explicit_port = parsed.port
    except ValueError:
        logger.warning('Push-notification URL is unparseable: %s', url)
        return False
    if parsed.scheme not in ('http', 'https'):
        logger.warning(
            'Push-notification URL scheme %r is not http/https: %s',
            parsed.scheme,
            url,
        )
        return False
    host = parsed.hostname
    if not host:
        logger.warning('Push-notification URL has no hostname: %s', url)
        return False
    port = explicit_port or (443 if parsed.scheme == 'https' else 80)
    try:
        loop = asyncio.get_running_loop()
        infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        logger.warning(
            'Push-notification host %r could not be resolved: %s', host, url
        )
        return False
    for info in infos:
        if _ip_is_blocked(str(info[4][0])):
            logger.warning(
                'Push-notification host %r resolves to a non-public address: %s',
                host,
                url,
            )
            return False
    return True
