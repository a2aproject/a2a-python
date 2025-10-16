"""Shared helpers for handling streaming HTTP responses."""

from __future__ import annotations

import json

from typing import Any

import httpx  # noqa: TC002

from httpx_sse import EventSource  # noqa: TC002

from a2a.client.errors import A2AClientHTTPError


SUCCESS_STATUS_MIN = 200
SUCCESS_STATUS_MAX = 300


async def ensure_streaming_response(event_source: EventSource) -> None:
    """Validate the initial streaming response before attempting SSE parsing."""
    response = event_source.response
    if not SUCCESS_STATUS_MIN <= response.status_code < SUCCESS_STATUS_MAX:
        error = await _build_http_error(response)
        raise error

    if not _has_event_stream_content_type(response):
        error = await _build_content_type_error(response)
        raise error


async def _build_http_error(response: httpx.Response) -> A2AClientHTTPError:
    body_text = await _read_body(response)
    json_payload: Any | None
    try:
        json_payload = response.json()
    except (json.JSONDecodeError, ValueError):
        json_payload = None

    message = _extract_message(response, json_payload, body_text)
    return A2AClientHTTPError(
        response.status_code,
        message,
        body=body_text,
        headers=dict(response.headers),
    )


async def _build_content_type_error(
    response: httpx.Response,
) -> A2AClientHTTPError:
    body_text = await _read_body(response)
    content_type = response.headers.get('content-type', None)
    descriptor = content_type or 'missing'
    message = f'Unexpected Content-Type {descriptor!r} for streaming response'
    return A2AClientHTTPError(
        response.status_code,
        message,
        body=body_text,
        headers=dict(response.headers),
    )


async def _read_body(response: httpx.Response) -> str | None:
    await response.aread()
    text = response.text
    return text if text else None


def _extract_message(
    response: httpx.Response,
    json_payload: Any | None,
    body_text: str | None,
) -> str:
    message: str | None = None
    if isinstance(json_payload, dict):
        title = _coerce_str(json_payload.get('title'))
        detail = _coerce_str(json_payload.get('detail'))
        if title and detail:
            message = f'{title}: {detail}'
        else:
            for key in ('message', 'detail', 'error', 'title'):
                value = _coerce_str(json_payload.get(key))
                if value:
                    message = value
                    break
    elif isinstance(json_payload, list):
        # Some APIs return a list of error descriptions—prefer the first string entry.
        for item in json_payload:
            value = _coerce_str(item)
            if value:
                message = value
                break

    if not message and body_text:
        stripped = body_text.strip()
        if stripped:
            message = stripped

    if not message:
        reason = getattr(response, 'reason_phrase', '') or ''
        message = reason or 'HTTP error'

    return message


def _coerce_str(value: Any) -> str | None:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return None


def _has_event_stream_content_type(response: httpx.Response) -> bool:
    content_type = response.headers.get('content-type', '')
    return 'text/event-stream' in content_type.lower()
