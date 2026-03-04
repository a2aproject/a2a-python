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
    A2AErrorToHttpStatus,
    A2AErrorToTitle,
    A2AErrorToTypeURI,
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
    assert result.media_type == 'application/problem+json'
    assert result.content == {
        'type': 'about:blank',
        'title': 'Invalid Request Error',
        'status': 400,
        'detail': 'Bad request',
    }


@pytest.mark.asyncio
async def test_rest_error_handler_with_data_extensions():
    """Test rest_error_handler maps A2AError.data to extension fields."""
    error = TaskNotFoundError(message='Task not found')
    # Dynamically attach data since __init__ no longer accepts it
    error.data = {'taskId': '123', 'retry': False}

    @rest_error_handler
    async def failing_func():
        raise error

    with patch('a2a.utils.error_handlers.JSONResponse', MockJSONResponse):
        result = await failing_func()

    assert isinstance(result, MockJSONResponse)
    assert result.status_code == 404
    assert result.media_type == 'application/problem+json'
    assert result.content == {
        'type': 'https://a2a-protocol.org/errors/task-not-found',
        'title': 'Task Not Found',
        'status': 404,
        'detail': 'Task not found',
        'taskId': '123',
        'retry': False,
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
    assert result.media_type == 'application/problem+json'
    assert result.content == {
        'type': 'about:blank',
        'title': 'Internal Error',
        'status': 500,
        'detail': 'Unknown exception',
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


def test_a2a_error_mappings():
    """Test A2A error mappings."""
    # HTTP Status
    assert A2AErrorToHttpStatus[InvalidRequestError] == 400
    assert A2AErrorToHttpStatus[MethodNotFoundError] == 404
    assert A2AErrorToHttpStatus[TaskNotFoundError] == 404
    assert A2AErrorToHttpStatus[InternalError] == 500

    # Type URI
    assert (
        A2AErrorToTypeURI[TaskNotFoundError]
        == 'https://a2a-protocol.org/errors/task-not-found'
    )

    # Title
    assert A2AErrorToTitle[TaskNotFoundError] == 'Task Not Found'
    assert A2AErrorToTitle[InvalidRequestError] == 'Invalid Request Error'
