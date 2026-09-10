import httpx
import pytest

from a2a.client.errors import A2AClientError
from a2a.client.transports.http_helpers import (
    _default_sse_error_handler,
    parse_sse_stream,
    send_http_stream_request,
)


def test_default_sse_error_handler():
    with pytest.raises(
        A2AClientError, match='SSE stream error event received: error_msg'
    ):
        _default_sse_error_handler('error_msg')


@pytest.mark.asyncio
async def test_parse_sse_stream_edge_cases():
    async def mock_aiter_lines():
        yield ': comment line (should be ignored)\n'
        yield 'event: custom_event\n'
        yield 'data:  hello\n'
        yield 'data: world\n'
        yield '\n'
        yield '\n'
        yield 'data: \n'
        yield '\n'

    response = httpx.Response(200)
    response.aiter_lines = mock_aiter_lines  # type: ignore

    events = [e async for e in parse_sse_stream(response)]
    assert events == [
        ('custom_event', ' hello\nworld'),
        ('message', ''),
    ]


@pytest.mark.asyncio
async def test_parse_sse_stream_flushes_last_event_without_blank_line():
    async def mock_aiter_lines():
        yield 'data: {"task":{"id":"t1"}}\n'

    response = httpx.Response(200)
    response.aiter_lines = mock_aiter_lines  # type: ignore

    events = [e async for e in parse_sse_stream(response)]
    assert events == [('message', '{"task":{"id":"t1"}}')]


@pytest.mark.asyncio
async def test_send_http_stream_request_non_sse(mocker):
    client = httpx.AsyncClient()
    request = httpx.Request('GET', 'http://test')
    response = httpx.Response(
        200,
        headers={'Content-Type': 'application/json'},
        content=b'plain error response',
        request=request,
    )

    mocker.patch(
        'a2a.client.transports.http_helpers._SSEEventSource.__aenter__',
        return_value=response,
    )
    mocker.patch(
        'a2a.client.transports.http_helpers._SSEEventSource.__aexit__',
        return_value=None,
    )

    chunks = [
        c async for c in send_http_stream_request(client, 'GET', 'http://test')
    ]
    assert chunks == ['plain error response']
