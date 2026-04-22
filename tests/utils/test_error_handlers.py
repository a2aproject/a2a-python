"""Tests for a2a.utils.error_handlers module."""

import logging

from unittest.mock import patch

import pytest

from a2a.types import (
    InternalError,
)
from a2a.utils.error_handlers import (
    rest_error_handler,
    rest_stream_error_handler,
)
from a2a.utils.errors import (
    InvalidRequestError,
)


class MockJSONResponse:
    def __init__(self, content, status_code, media_type=None):
        self.content = content
        self.status_code = status_code
        self.media_type = media_type


class MockEventSourceResponse:
    def __init__(self, body_iterator):
        self.body_iterator = body_iterator


@pytest.mark.asyncio
async def test_rest_error_handler_server_error():
    """Test rest_error_handler with A2AError."""
    error = InvalidRequestError(message='Bad request')

    @rest_error_handler
    async def failing_func():
        raise error

    with patch('a2a.utils.error_handlers.JSONResponse', MockJSONResponse):
        result = await failing_func()

    assert isinstance(result, MockJSONResponse)
    assert result.status_code == 400
    assert result.media_type == 'application/json'
    assert result.content == {
        'error': {
            'code': 400,
            'status': 'INVALID_ARGUMENT',
            'message': 'Bad request',
            'details': [
                {
                    '@type': 'type.googleapis.com/google.rpc.ErrorInfo',
                    'reason': 'INVALID_REQUEST',
                    'domain': 'a2a-protocol.org',
                    'metadata': {},
                }
            ],
        }
    }


@pytest.mark.asyncio
async def test_rest_error_handler_unknown_exception():
    """Test rest_error_handler with unknown exception."""

    @rest_error_handler
    async def failing_func():
        raise ValueError('Unexpected error')

    with patch('a2a.utils.error_handlers.JSONResponse', MockJSONResponse):
        result = await failing_func()

    assert isinstance(result, MockJSONResponse)
    assert result.status_code == 500
    assert result.media_type == 'application/json'
    assert result.content == {
        'error': {
            'code': 500,
            'status': 'INTERNAL',
            'message': 'unknown exception',
        }
    }


@pytest.mark.asyncio
async def test_rest_stream_error_handler_server_error():
    """Test rest_stream_error_handler with A2AError."""
    error = InternalError(message='Internal server error')

    @rest_stream_error_handler
    async def failing_stream():
        raise error

    response = await failing_stream()

    assert response.status_code == 500


@pytest.mark.asyncio
async def test_rest_stream_error_handler_reraises_exception():
    """Test rest_stream_error_handler catches other exceptions and returns JSONResponse."""

    @rest_stream_error_handler
    async def failing_stream():
        raise RuntimeError('Stream failed')

    response = await failing_stream()
    assert response.status_code == 500


@pytest.mark.asyncio
async def test_rest_error_handler_success():
    """Test rest_error_handler on success."""

    @rest_error_handler
    async def successful_func():
        return 'success'

    result = await successful_func()
    assert result == 'success'


@pytest.mark.asyncio
async def test_rest_stream_error_handler_generator_error(caplog):
    """Test rest_stream_error_handler catches error during async generation after first success."""
    error = InternalError(message='Stream error during generation')

    async def failing_generator():
        yield 'success chunk 1'
        raise error

    @rest_stream_error_handler
    async def successful_prep_failing_stream():
        return MockEventSourceResponse(failing_generator())

    response = await successful_prep_failing_stream()

    # Assert it returns successfully
    assert isinstance(response, MockEventSourceResponse)

    # Now consume the stream
    chunks = []
    with (
        caplog.at_level(logging.ERROR),
        pytest.raises(InternalError) as exc_info,
    ):
        async for chunk in response.body_iterator:
            chunks.append(chunk)  # noqa: PERF401
    assert chunks == ['success chunk 1']
    assert exc_info.value == error


@pytest.mark.asyncio
async def test_rest_stream_error_handler_generator_unknown_error(caplog):
    """Test rest_stream_error_handler catches unknown error during async generation."""

    async def failing_generator():
        yield 'success chunk 1'
        raise RuntimeError('Unknown stream failure')

    @rest_stream_error_handler
    async def successful_prep_failing_stream():
        return MockEventSourceResponse(failing_generator())

    response = await successful_prep_failing_stream()

    chunks = []
    with (
        caplog.at_level(logging.ERROR),
        pytest.raises(RuntimeError, match='Unknown stream failure'),
    ):
        async for chunk in response.body_iterator:
            chunks.append(chunk)  # noqa: PERF401
    assert chunks == ['success chunk 1']
    assert 'Unknown streaming error occurred' in caplog.text


@pytest.mark.asyncio
async def test_rest_stream_error_handler_success():
    """Test rest_stream_error_handler on success."""

    @rest_stream_error_handler
    async def successful_stream():
        return 'success_stream'

    result = await successful_stream()
    assert result == 'success_stream'
