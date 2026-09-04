"""Shared policy for screening client-supplied push-notification URLs."""

import asyncio
import ipaddress
import logging
import socket
import urllib.parse


logger = logging.getLogger(__name__)


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


async def validate_push_notification_url(url: str) -> bool:
    """Return True if a push-notification URL is safe to fetch.

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
