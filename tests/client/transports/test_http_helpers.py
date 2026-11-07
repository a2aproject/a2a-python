import json

import httpx
import pytest

from a2a.client.client import ClientCallContext
from a2a.client.errors import A2AClientError
from a2a.client.transports.http_helpers import (
    _SSEEventSource,
    _default_sse_error_handler,
    get_http_args,
    parse_sse_stream,
    send_http_stream_request,
)
from a2a.utils import json_utils


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


def test_get_http_args_passes_timeout_through_unwrapped():
    """The timeout stays a bare float, for the client itself to wrap."""
    http_kwargs = get_http_args(ClientCallContext(timeout=5.0))

    assert http_kwargs['timeout'] == 5.0
    assert not isinstance(http_kwargs['timeout'], httpx.Timeout)

    request = httpx.AsyncClient().build_request(
        'GET', 'http://test', **http_kwargs
    )
    assert request.extensions['timeout'] == {
        'connect': 5.0,
        'read': 5.0,
        'write': 5.0,
        'pool': 5.0,
    }


def test_sse_event_source_sets_default_stream_headers():
    source = _SSEEventSource(httpx.AsyncClient(), 'GET', 'http://test')

    assert source._request.headers['Accept'] == 'text/event-stream'
    assert source._request.headers['Cache-Control'] == 'no-store'


@pytest.mark.parametrize(
    'caller_spelling', ['accept', 'Accept', 'ACCEPT', 'AcCePt']
)
def test_sse_event_source_keeps_caller_accept_whatever_its_case(
    caller_spelling: str,
):
    """A caller's own Accept wins, and is never joined by a second one.

    HTTP header names are case-insensitive, so matching the caller's
    spelling exactly is not enough to detect that they already set one.
    """
    source = _SSEEventSource(
        httpx.AsyncClient(),
        'GET',
        'http://test',
        headers={caller_spelling: 'application/json'},
    )

    assert source._request.headers.get_list('accept') == ['application/json']


@pytest.mark.asyncio
@pytest.mark.parametrize('separator', ['\u2028', '\u2029', '\u0085'])
@pytest.mark.parametrize('chunk_size', [1, 7, 4096])
async def test_parse_sse_stream_keeps_unicode_line_separators(
    separator: str, chunk_size: int
) -> None:
    """Regression test for #1432.

    A streamed event whose text contains U+2028, U+2029 or U+0085 must arrive
    as one ``data:`` line, because SSE only ends lines on CR, LF and CRLF.
    """
    payload = json_utils.dumps({'text': f'before{separator}after'})
    frame = f'data: {payload}\n\n'.encode()

    async def body():
        for i in range(0, len(frame), chunk_size):
            yield frame[i : i + chunk_size]

    response = httpx.Response(
        200,
        headers={'content-type': 'text/event-stream'},
        content=body(),
    )

    events = [event async for event in parse_sse_stream(response)]

    assert events == [('message', payload)]
    assert json.loads(events[0][1]) == {'text': f'before{separator}after'}
