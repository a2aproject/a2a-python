"""Tests for the validate_push_notification_url compatibility wrapper.

The wrapper delegates to PushNotificationUrlValidator and keeps the
historical boolean, fail-closed contract (issue #1023 refactor).
"""

import a2a.utils.push_url_validator as push_url_validator_module
import pytest

from a2a.utils.push_url_validator import validate_push_notification_url


PUBLIC_IP = '93.184.216.34'


def _pin_resolver(monkeypatch, *ips: str) -> None:
    """Pin the module singleton's resolver to deterministic addresses."""

    async def resolve(host: str, port: int):
        return [(2, 1, 6, '', (ip, port)) for ip in ips]

    monkeypatch.setattr(
        push_url_validator_module._validator._validator,
        '_resolver',
        resolve,
    )


def _pin_failing_resolver(monkeypatch) -> None:
    """Pin the module singleton's resolver to a failing lookup."""

    async def resolve(host: str, port: int):
        raise OSError('no DNS')

    monkeypatch.setattr(
        push_url_validator_module._validator._validator,
        '_resolver',
        resolve,
    )


@pytest.mark.asyncio
async def test_returns_true_for_public_http_url(monkeypatch):
    _pin_resolver(monkeypatch, PUBLIC_IP)
    assert await validate_push_notification_url('http://example.com/hook')


@pytest.mark.asyncio
async def test_returns_false_for_non_http_scheme(monkeypatch):
    _pin_resolver(monkeypatch, PUBLIC_IP)
    assert not await validate_push_notification_url('ftp://example.com/file')


@pytest.mark.asyncio
async def test_returns_false_when_resolution_fails(monkeypatch):
    _pin_failing_resolver(monkeypatch)
    assert not await validate_push_notification_url('http://example.com/hook')
