"""Backward-compatible entry point for push-notification URL screening.

The implementation now lives in :mod:`a2a.utils.url_validation` as a
composition of :class:`a2a.utils.url_validation.UrlValidator` rules
(issue #1023); this module keeps the historical boolean, fail-closed
entry point.
"""

import logging

from a2a.utils.url_validation import (
    InvalidUrlError,
    PushNotificationUrlValidator,
)


logger = logging.getLogger(__name__)

_validator = PushNotificationUrlValidator()


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
        return await _validator.validate(url)
    except InvalidUrlError:
        logger.warning('Push-notification URL rejected: %s', url)
        return False
