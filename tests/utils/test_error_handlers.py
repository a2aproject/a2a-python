"""Tests for a2a.utils.error_handlers module."""

from unittest.mock import patch

import pytest

from a2a.types import (
    InternalError,
    InvalidRequestError,
    MethodNotFoundError,
    TaskNotFoundError,
)
from a2a.utils.error_handlers import (
    A2AErrorToHttpStatus,
    rest_error_handler,
    rest_stream_error_handler,
)
from a2a.utils.errors import ServerError


class MockJSONResponse:
    def __init__(self, content, status_code):
        self.content = content
        self.status_code = status_code


@pytest.mark.asyncio
async def test_rest_error_handler_server_error():
    """Test rest_error_handler with ServerError."""
    error = InvalidRequestError(message='Bad request')

    @rest_error_handler
    async def failing_func():
        raise ServerError(error=error)

    with patch('a2a.utils.error_handlers.JSONResponse', MockJSONResponse):
        result = await failing_func()

    assert isinstance(result, MockJSONResponse)
    assert result.status_code == 400
    assert result.content == {'message': 'Bad request'}


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
    assert result.content == {'message': 'unknown exception'}


@pytest.mark.asyncio
async def test_rest_stream_error_handler_server_error():
    """Test rest_stream_error_handler with ServerError."""
    error = InternalError(message='Internal server error')

    @rest_stream_error_handler
    async def failing_stream():
        raise ServerError(error=error)

    with pytest.raises(ServerError) as exc_info:
        await failing_stream()

    assert exc_info.value.error == error


@pytest.mark.asyncio
async def test_rest_stream_error_handler_reraises_exception():
    """Test rest_stream_error_handler reraises other exceptions."""

    @rest_stream_error_handler
    async def failing_stream():
        raise RuntimeError('Stream failed')

    with pytest.raises(RuntimeError, match='Stream failed'):
        await failing_stream()


def test_a2a_error_to_http_status_mapping():
    """Test A2AErrorToHttpStatus mapping."""
    assert A2AErrorToHttpStatus[InvalidRequestError] == 400
    assert A2AErrorToHttpStatus[MethodNotFoundError] == 404
    assert A2AErrorToHttpStatus[TaskNotFoundError] == 404
    assert A2AErrorToHttpStatus[InternalError] == 500
