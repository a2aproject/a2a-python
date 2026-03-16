"""Tests for a2a.utils.error_handlers module."""

from unittest.mock import patch

import pytest

from a2a.types import (
    InternalError,
    TaskNotFoundError,
)
from a2a.utils.errors import (
    InvalidRequestError,
    MethodNotFoundError,
)
from a2a.utils.error_handlers import (
    rest_error_handler,
    rest_stream_error_handler,
)


class MockJSONResponse:
    def __init__(self, content, status_code, media_type=None):
        self.content = content
        self.status_code = status_code
        self.media_type = media_type


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

    with pytest.raises(InternalError) as exc_info:
        await failing_stream()

    assert exc_info.value == error


@pytest.mark.asyncio
async def test_rest_stream_error_handler_reraises_exception():
    """Test rest_stream_error_handler reraises other exceptions."""

    @rest_stream_error_handler
    async def failing_stream():
        raise RuntimeError('Stream failed')

    with pytest.raises(RuntimeError, match='Stream failed'):
        await failing_stream()
