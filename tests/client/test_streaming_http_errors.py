"""Tests for a2a.client.errors module."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from a2a.client import create_text_message_object
from a2a.client.errors import A2AClientHTTPError
from a2a.client.transports.rest import RestTransport
from a2a.types import MessageSendParams


@dataclass
class DummyServerSentEvent:
    data: str


class MockEventSource:
    def __init__(
        self,
        response: httpx.Response,
        events: list[Any] | None = None,
        error: Exception | None = None,
    ):
        self.response = response
        self._events = events or []
        self._error = error

    async def __aenter__(self) -> 'MockEventSource':
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def aiter_sse(self) -> AsyncIterator[Any]:
        if self._error:
            raise self._error
        for event in self._events:
            yield event


def make_response(
    status: int,
    *,
    json_body: dict[str, Any] | None = None,
    text_body: str | None = None,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    request = httpx.Request('POST', 'https://api.example.com/v1/message:stream')
    if json_body is not None:
        response = httpx.Response(
            status,
            json=json_body,
            headers=headers,
            request=request,
        )
    else:
        response = httpx.Response(
            status,
            content=text_body.encode() if text_body else b'',
            headers=headers,
            request=request,
        )
    return response


def make_transport() -> RestTransport:
    httpx_client = AsyncMock(spec=httpx.AsyncClient)
    transport = RestTransport(
        httpx_client=httpx_client, url='https://api.example.com'
    )
    transport._prepare_send_message = AsyncMock(return_value=({}, {}))
    return transport


async def collect_stream(transport: RestTransport, params: MessageSendParams):
    return [item async for item in transport.send_message_streaming(params)]


def patch_stream_context(event_source: MockEventSource):
    @asynccontextmanager
    async def fake_aconnect_sse(*_: Any, **__: Any):
        yield event_source

    return patch(
        'a2a.client.transports.rest.aconnect_sse', new=fake_aconnect_sse
    )


@pytest.mark.parametrize(
    ('status', 'body', 'expected'),
    [
        (401, {'error': 'invalid_token'}, 'invalid_token'),
        (500, {'message': 'DB down'}, 'DB down'),
        (503, {'detail': 'Service unavailable'}, 'Service unavailable'),
        (
            404,
            {'title': 'Not Found', 'detail': 'No such task'},
            'Not Found: No such task',
        ),
    ],
)
@pytest.mark.asyncio
async def test_streaming_surfaces_http_errors(
    status: int, body: dict[str, Any], expected: str
):
    transport = make_transport()
    params = MessageSendParams(
        message=create_text_message_object(content='Hello')
    )
    response = make_response(status, json_body=body)
    event_source = MockEventSource(response)

    with patch_stream_context(event_source), pytest.raises(
        A2AClientHTTPError
    ) as exc_info:
        await collect_stream(transport, params)

    error = exc_info.value
    assert error.status == status
    assert expected in error.message
    assert error.body is not None
    assert str(status) in str(error)


@pytest.mark.asyncio
async def test_streaming_rejects_wrong_content_type():
    transport = make_transport()
    params = MessageSendParams(
        message=create_text_message_object(content='Hello')
    )
    response = make_response(
        200,
        json_body={'message': 'not a stream'},
        headers={'content-type': 'application/json'},
    )
    event_source = MockEventSource(response)

    with patch_stream_context(event_source), pytest.raises(
        A2AClientHTTPError
    ) as exc_info:
        await collect_stream(transport, params)

    error = exc_info.value
    assert error.status == 200
    assert 'Unexpected Content-Type' in error.message
    assert 'application/json' in error.message


@pytest.mark.asyncio
async def test_streaming_success_path_unchanged():
    transport = make_transport()
    params = MessageSendParams(
        message=create_text_message_object(content='Hello')
    )
    response = make_response(
        200,
        text_body='event-stream',
        headers={'content-type': 'text/event-stream'},
    )
    events = [DummyServerSentEvent(data='{"foo":"bar"}')]
    event_source = MockEventSource(response, events=events)

    with (
        patch_stream_context(event_source),
        patch(
            'a2a.client.transports.rest.Parse',
            side_effect=lambda data, obj: obj,
        ) as mock_parse,
        patch(
            'a2a.client.transports.rest.proto_utils.FromProto.stream_response',
            return_value={'result': 'ok'},
        ) as mock_from_proto,
    ):
        results = await collect_stream(transport, params)

    assert results == [{'result': 'ok'}]
    mock_parse.assert_called()
    mock_from_proto.assert_called()
