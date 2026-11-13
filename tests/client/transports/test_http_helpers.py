from collections.abc import AsyncIterator

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


def test_default_sse_error_handler():
    with pytest.raises(
        A2AClientError, match='SSE stream error event received: error_msg'
    ):
        _default_sse_error_handler('error_msg')


@pytest.mark.asyncio
async def test_parse_sse_stream_edge_cases():
    async def mock_aiter_text():
        yield ': comment line (should be ignored)\n'
        yield 'event: custom_event\n'
        yield 'data:  hello\n'
        yield 'data: world\n'
        yield '\n'
        yield '\n'
        yield 'data: \n'
        yield '\n'

    response = httpx.Response(200)
    response.aiter_text = mock_aiter_text  # type: ignore

    events = [e async for e in parse_sse_stream(response)]
    assert events == [
        ('custom_event', ' hello\nworld'),
        ('message', ''),
    ]


async def _byte_chunks(chunks: list[bytes]) -> AsyncIterator[bytes]:
    for chunk in chunks:
        yield chunk


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('chunks', 'expected'),
    [
        pytest.param(
            ['data: a b c\x85d\n\n'.encode()],
            [('message', 'a b c\x85d')],
            id='unicode-line-separators-stay-in-data',
        ),
        pytest.param(
            [b'data: a\xe2\x80', b'\xa8b\r', b'\ndata: c\r\r'],
            [('message', 'a b\nc')],
            id='crlf-and-character-split-across-chunks',
        ),
        pytest.param(
            [b'data: a\r'],
            [],
            id='event-without-blank-line-is-not-dispatched',
        ),
    ],
)
async def test_parse_sse_stream_ends_lines_only_at_cr_or_lf(
    chunks: list[bytes], expected: list[tuple[str, str]]
) -> None:
    """Only CRLF, LF and CR end a line, as the SSE specification requires."""
    response = httpx.Response(200, content=_byte_chunks(chunks))

    events = [e async for e in parse_sse_stream(response)]

    assert events == expected


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_parse_sse_stream_long_data_line_over_many_chunks() -> None:
    """A multi-megabyte data: line read in small chunks stays linear."""
    payload = 'x' * (2 * 1024 * 1024)
    body = f'data: {payload}\r\n\r\n'.encode()
    chunk_size = 512
    chunks = [body[i : i + chunk_size] for i in range(0, len(body), chunk_size)]
    response = httpx.Response(200, content=_byte_chunks(chunks))

    # Re-joining and re-scanning the whole line on every chunk took tens of
    # seconds here; the linear version takes well under a second.
    events = [e async for e in parse_sse_stream(response)]

    assert events == [('message', payload)]


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
