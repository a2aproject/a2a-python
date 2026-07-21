"""Optional validators for AgentCard instances before client creation."""

from __future__ import annotations

import ipaddress

from collections.abc import Callable
from urllib.parse import urlparse

from a2a.types.a2a_pb2 import AgentCard


CardValidator = Callable[[AgentCard], None]


class InvalidAgentCardError(ValueError):
    """Raised when an AgentCard fails a registered validation hook."""


def _iter_card_urls(card: AgentCard) -> list[str]:
    urls = [
        interface.url
        for interface in card.supported_interfaces
        if interface.url
    ]
    if card.documentation_url:
        urls.append(card.documentation_url)
    if card.icon_url:
        urls.append(card.icon_url)
    return urls


def reject_non_https_urls(card: AgentCard) -> None:
    """Reject cards whose interface or metadata URLs are not https://."""
    for url in _iter_card_urls(card):
        if urlparse(url).scheme != 'https':
            raise InvalidAgentCardError(
                f'Card URL must use https, got: {url!r}'
            )


def reject_private_urls(card: AgentCard) -> None:
    """Reject cards whose URLs use private, loopback, or link-local IP hosts."""
    for url in _iter_card_urls(card):
        _check_not_private(url)


def _check_not_private(url: str) -> None:
    parsed = urlparse(url)
    host = parsed.hostname
    if host is None:
        raise InvalidAgentCardError(f'URL has no hostname: {url!r}')
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
        raise InvalidAgentCardError(
            f'URL points to a private or loopback address: {url!r}'
        )


def require_supported_interfaces(card: AgentCard) -> None:
    """Reject cards that do not declare at least one supported interface."""
    if not card.supported_interfaces:
        raise InvalidAgentCardError(
            'Card must declare at least one supported interface.'
        )
    for interface in card.supported_interfaces:
        if not interface.url:
            raise InvalidAgentCardError(
                'Each supported interface must include a URL.'
            )
        if not interface.protocol_binding:
            raise InvalidAgentCardError(
                'Each supported interface must include a protocol binding.'
            )


def validate_card(
    card: AgentCard, validators: list[CardValidator] | None
) -> None:
    """Run registered validators against an AgentCard."""
    for validator in validators or []:
        validator(card)
