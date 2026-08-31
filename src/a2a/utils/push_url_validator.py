"""Shared policy for screening client-supplied push-notification URLs."""

import asyncio
import ipaddress
import socket
import urllib.parse


def _ip_is_blocked(ip_str: str) -> bool:
    """Whether an address is not a public unicast destination."""
    try:
        addr = ipaddress.ip_address(ip_str.split('%', maxsplit=1)[0])
    except ValueError:
        return True
    return (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    )


async def push_url_validation_error(url: str) -> str | None:
    """Return an error string if a push-notification URL is not safe.

    Blocks non-HTTP(S) schemes and hosts that resolve to loopback,
    link-local, private, reserved, multicast, or unspecified addresses
    (e.g. 169.254.169.254 cloud metadata, internal services). A host
    that cannot be resolved is rejected: the POST would fail anyway,
    and failing closed avoids treating resolution errors as a bypass.

    IPv4-mapped IPv6 forms are covered: ``ipaddress`` maps them to the
    underlying IPv4 address, so the ``is_private``/``is_loopback``
    checks apply to the mapped value.

    Uses the running event-loop resolver so request handlers and the
    sender stay non-blocking. Deployments can pass this function as
    ``push_url_validator`` on ``DefaultRequestHandler`` /
    ``DefaultRequestHandlerV2`` / ``BasePushNotificationSender``.
    The default on those constructors is ``None`` (no library
    screening).
    """
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError:
        return 'unparseable URL'
    if parsed.scheme not in ('http', 'https'):
        return f"scheme '{parsed.scheme}' is not http/https"
    host = parsed.hostname
    if not host:
        return 'no hostname'
    port = parsed.port or (443 if parsed.scheme == 'https' else 80)
    try:
        loop = asyncio.get_running_loop()
        infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        return f"host '{host}' could not be resolved"
    for info in infos:
        if _ip_is_blocked(str(info[4][0])):
            return f"host '{host}' resolves to a non-public address"
    return None
