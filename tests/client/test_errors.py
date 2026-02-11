from typing import NoReturn
from unittest.mock import MagicMock

import pytest

from a2a.client import A2AClientError, A2AClientHTTPError, A2AClientJSONError
from a2a.client.errors import (
    A2AClientInvalidArgsError,
    A2AClientInvalidStateError,
    A2AClientJSONRPCError,
    A2AClientTimeoutError,
)


class TestA2AClientError:
    """Test cases for the base A2AClientError class."""

    def test_instantiation(self) -> None:
        """Test that A2AClientError can be instantiated."""
        error = A2AClientError('Test error message')
        assert isinstance(error, Exception)
        assert str(error) == 'Test error message'

    def test_inheritance(self) -> None:
        """Test that A2AClientError inherits from Exception."""
        error = A2AClientError()
        assert isinstance(error, Exception)


class TestA2AClientHTTPError:
    """Test cases for A2AClientHTTPError class."""

    def test_instantiation(self) -> None:
        """Test that A2AClientHTTPError can be instantiated with status_code and message."""
        error = A2AClientHTTPError(404, 'Not Found')
        assert isinstance(error, A2AClientError)
        assert error.status_code == 404
        assert error.message == 'Not Found'

    def test_message_formatting(self) -> None:
        """Test that the error message is formatted correctly."""
        error = A2AClientHTTPError(500, 'Internal Server Error')
        assert str(error) == 'HTTP Error 500: Internal Server Error'

    def test_repr(self) -> None:
        """Test that __repr__ shows structured attributes."""
        error = A2AClientHTTPError(404, 'Not Found')
        assert (
            repr(error)
            == "A2AClientHTTPError(status_code=404, message='Not Found')"
        )

    def test_inheritance(self) -> None:
        """Test that A2AClientHTTPError inherits from A2AClientError."""
        error = A2AClientHTTPError(400, 'Bad Request')
        assert isinstance(error, A2AClientError)

    def test_with_empty_message(self) -> None:
        """Test behavior with an empty message."""
        error = A2AClientHTTPError(403, '')
        assert error.status_code == 403
        assert error.message == ''
        assert str(error) == 'HTTP Error 403: '

    def test_with_various_status_codes(self) -> None:
        """Test with different HTTP status codes."""
        test_cases = [
            (200, 'OK'),
            (201, 'Created'),
            (400, 'Bad Request'),
            (401, 'Unauthorized'),
            (403, 'Forbidden'),
            (404, 'Not Found'),
            (500, 'Internal Server Error'),
            (503, 'Service Unavailable'),
        ]

        for status_code, message in test_cases:
            error = A2AClientHTTPError(status_code, message)
            assert error.status_code == status_code
            assert error.message == message
            assert str(error) == f'HTTP Error {status_code}: {message}'


class TestA2AClientJSONError:
    """Test cases for A2AClientJSONError class."""

    def test_instantiation(self) -> None:
        """Test that A2AClientJSONError can be instantiated with a message."""
        error = A2AClientJSONError('Invalid JSON format')
        assert isinstance(error, A2AClientError)
        assert error.message == 'Invalid JSON format'

    def test_message_formatting(self) -> None:
        """Test that the error message is formatted correctly."""
        error = A2AClientJSONError('Missing required field')
        assert str(error) == 'JSON Error: Missing required field'

    def test_repr(self) -> None:
        """Test that __repr__ shows structured attributes."""
        error = A2AClientJSONError('Invalid JSON format')
        assert (
            repr(error) == "A2AClientJSONError(message='Invalid JSON format')"
        )

    def test_inheritance(self) -> None:
        """Test that A2AClientJSONError inherits from A2AClientError."""
        error = A2AClientJSONError('Parsing error')
        assert isinstance(error, A2AClientError)

    def test_with_empty_message(self) -> None:
        """Test behavior with an empty message."""
        error = A2AClientJSONError('')
        assert error.message == ''
        assert str(error) == 'JSON Error: '

    def test_with_various_messages(self) -> None:
        """Test with different error messages."""
        test_messages = [
            'Malformed JSON',
            'Missing required fields',
            'Invalid data type',
            'Unexpected JSON structure',
            'Empty JSON object',
        ]

        for message in test_messages:
            error = A2AClientJSONError(message)
            assert error.message == message
            assert str(error) == f'JSON Error: {message}'


class TestA2AClientTimeoutError:
    """Test cases for A2AClientTimeoutError class."""

    def test_instantiation(self) -> None:
        """Test that A2AClientTimeoutError can be instantiated with a message."""
        error = A2AClientTimeoutError('Request timed out')
        assert isinstance(error, A2AClientError)
        assert error.message == 'Request timed out'

    def test_message_formatting(self) -> None:
        """Test that the error message is formatted correctly."""
        error = A2AClientTimeoutError('Connection timed out after 30s')
        assert str(error) == 'Timeout Error: Connection timed out after 30s'

    def test_repr(self) -> None:
        """Test that __repr__ shows structured attributes."""
        error = A2AClientTimeoutError('Request timed out')
        assert (
            repr(error) == "A2AClientTimeoutError(message='Request timed out')"
        )

    def test_inheritance(self) -> None:
        """Test that A2AClientTimeoutError inherits from A2AClientError."""
        error = A2AClientTimeoutError('timeout')
        assert isinstance(error, A2AClientError)

    def test_with_empty_message(self) -> None:
        """Test behavior with an empty message."""
        error = A2AClientTimeoutError('')
        assert error.message == ''
        assert str(error) == 'Timeout Error: '


class TestA2AClientInvalidArgsError:
    """Test cases for A2AClientInvalidArgsError class."""

    def test_instantiation(self) -> None:
        """Test that A2AClientInvalidArgsError can be instantiated."""
        error = A2AClientInvalidArgsError('Missing required param')
        assert isinstance(error, A2AClientError)
        assert error.message == 'Missing required param'

    def test_message_formatting(self) -> None:
        """Test that the error message is formatted correctly."""
        error = A2AClientInvalidArgsError('Invalid type for param X')
        assert str(error) == 'Invalid arguments error: Invalid type for param X'

    def test_repr(self) -> None:
        """Test that __repr__ shows structured attributes."""
        error = A2AClientInvalidArgsError('Missing required param')
        assert (
            repr(error)
            == "A2AClientInvalidArgsError(message='Missing required param')"
        )

    def test_inheritance(self) -> None:
        """Test that A2AClientInvalidArgsError inherits from A2AClientError."""
        error = A2AClientInvalidArgsError('bad args')
        assert isinstance(error, A2AClientError)

    def test_with_empty_message(self) -> None:
        """Test behavior with an empty message."""
        error = A2AClientInvalidArgsError('')
        assert error.message == ''
        assert str(error) == 'Invalid arguments error: '


class TestA2AClientInvalidStateError:
    """Test cases for A2AClientInvalidStateError class."""

    def test_instantiation(self) -> None:
        """Test that A2AClientInvalidStateError can be instantiated."""
        error = A2AClientInvalidStateError('Client not initialized')
        assert isinstance(error, A2AClientError)
        assert error.message == 'Client not initialized'

    def test_message_formatting(self) -> None:
        """Test that the error message is formatted correctly."""
        error = A2AClientInvalidStateError('Already closed')
        assert str(error) == 'Invalid state error: Already closed'

    def test_repr(self) -> None:
        """Test that __repr__ shows structured attributes."""
        error = A2AClientInvalidStateError('Client not initialized')
        assert (
            repr(error)
            == "A2AClientInvalidStateError(message='Client not initialized')"
        )

    def test_inheritance(self) -> None:
        """Test that A2AClientInvalidStateError inherits from A2AClientError."""
        error = A2AClientInvalidStateError('bad state')
        assert isinstance(error, A2AClientError)

    def test_with_empty_message(self) -> None:
        """Test behavior with an empty message."""
        error = A2AClientInvalidStateError('')
        assert error.message == ''
        assert str(error) == 'Invalid state error: '


class TestA2AClientJSONRPCError:
    """Test cases for A2AClientJSONRPCError class."""

    def _make_error_response(
        self, code: int = -32600, message: str = 'Invalid Request', data=None
    ):
        """Helper to create a mock JSONRPCErrorResponse."""
        inner_error = MagicMock()
        inner_error.code = code
        inner_error.message = message
        inner_error.data = data

        response = MagicMock()
        response.error = inner_error
        return response

    def test_instantiation(self) -> None:
        """Test that A2AClientJSONRPCError can be instantiated."""
        response = self._make_error_response()
        error = A2AClientJSONRPCError(response)
        assert isinstance(error, A2AClientError)
        assert error.error == response.error

    def test_repr(self) -> None:
        """Test that __repr__ shows the JSON-RPC error object."""
        response = self._make_error_response(-32601, 'Method not found')
        error = A2AClientJSONRPCError(response)
        result = repr(error)
        assert result == f'A2AClientJSONRPCError({response.error!r})'

    def test_inheritance(self) -> None:
        """Test that A2AClientJSONRPCError inherits from A2AClientError."""
        response = self._make_error_response()
        error = A2AClientJSONRPCError(response)
        assert isinstance(error, A2AClientError)

    def test_with_empty_message(self) -> None:
        """Test behavior with an empty message."""
        response = self._make_error_response(message='')
        error = A2AClientJSONRPCError(response)
        assert error.error.message == ''
        assert str(error) == f'JSON-RPC Error {response.error}'


class TestExceptionHierarchy:
    """Test the exception hierarchy and relationships."""

    def test_exception_hierarchy(self) -> None:
        """Test that the exception hierarchy is correct."""
        assert issubclass(A2AClientError, Exception)
        assert issubclass(A2AClientHTTPError, A2AClientError)
        assert issubclass(A2AClientJSONError, A2AClientError)
        assert issubclass(A2AClientTimeoutError, A2AClientError)
        assert issubclass(A2AClientInvalidArgsError, A2AClientError)
        assert issubclass(A2AClientInvalidStateError, A2AClientError)
        assert issubclass(A2AClientJSONRPCError, A2AClientError)

    def test_catch_specific_exception(self) -> None:
        """Test that specific exceptions can be caught."""
        try:
            raise A2AClientHTTPError(404, 'Not Found')
        except A2AClientHTTPError as e:
            assert e.status_code == 404
            assert e.message == 'Not Found'

    def test_catch_base_exception(self) -> None:
        """Test that derived exceptions can be caught as base exception."""
        exceptions = [
            A2AClientHTTPError(404, 'Not Found'),
            A2AClientJSONError('Invalid JSON'),
            A2AClientTimeoutError('Timed out'),
            A2AClientInvalidArgsError('Bad args'),
            A2AClientInvalidStateError('Bad state'),
        ]

        for raised_error in exceptions:
            try:
                raise raised_error
            except A2AClientError as e:
                assert isinstance(e, A2AClientError)


class TestExceptionRaising:
    """Test cases for raising and handling the exceptions."""

    def test_raising_http_error(self) -> NoReturn:
        """Test raising an HTTP error and checking its properties."""
        with pytest.raises(A2AClientHTTPError) as excinfo:
            raise A2AClientHTTPError(429, 'Too Many Requests')

        error = excinfo.value
        assert error.status_code == 429
        assert error.message == 'Too Many Requests'
        assert str(error) == 'HTTP Error 429: Too Many Requests'

    def test_raising_json_error(self) -> NoReturn:
        """Test raising a JSON error and checking its properties."""
        with pytest.raises(A2AClientJSONError) as excinfo:
            raise A2AClientJSONError('Invalid format')

        error = excinfo.value
        assert error.message == 'Invalid format'
        assert str(error) == 'JSON Error: Invalid format'

    def test_raising_base_error(self) -> NoReturn:
        """Test raising the base error."""
        with pytest.raises(A2AClientError) as excinfo:
            raise A2AClientError('Generic client error')

        assert str(excinfo.value) == 'Generic client error'

    def test_raising_timeout_error(self) -> NoReturn:
        """Test raising a timeout error and checking its properties."""
        with pytest.raises(A2AClientTimeoutError) as excinfo:
            raise A2AClientTimeoutError('Connection timed out')

        error = excinfo.value
        assert error.message == 'Connection timed out'
        assert str(error) == 'Timeout Error: Connection timed out'


# Additional parametrized tests for more comprehensive coverage


@pytest.mark.parametrize(
    'status_code,message,expected_str,expected_repr',
    [
        (
            400,
            'Bad Request',
            'HTTP Error 400: Bad Request',
            "A2AClientHTTPError(status_code=400, message='Bad Request')",
        ),
        (
            404,
            'Not Found',
            'HTTP Error 404: Not Found',
            "A2AClientHTTPError(status_code=404, message='Not Found')",
        ),
        (
            500,
            'Server Error',
            'HTTP Error 500: Server Error',
            "A2AClientHTTPError(status_code=500, message='Server Error')",
        ),
    ],
)
def test_http_error_parametrized(
    status_code: int, message: str, expected_str: str, expected_repr: str
) -> None:
    """Parametrized test for HTTP errors with different status codes."""
    error = A2AClientHTTPError(status_code, message)
    assert error.status_code == status_code
    assert error.message == message
    assert str(error) == expected_str
    assert repr(error) == expected_repr


@pytest.mark.parametrize(
    'message,expected_str,expected_repr',
    [
        (
            'Missing field',
            'JSON Error: Missing field',
            "A2AClientJSONError(message='Missing field')",
        ),
        (
            'Invalid type',
            'JSON Error: Invalid type',
            "A2AClientJSONError(message='Invalid type')",
        ),
        (
            'Parsing failed',
            'JSON Error: Parsing failed',
            "A2AClientJSONError(message='Parsing failed')",
        ),
    ],
)
def test_json_error_parametrized(
    message: str, expected_str: str, expected_repr: str
) -> None:
    """Parametrized test for JSON errors with different messages."""
    error = A2AClientJSONError(message)
    assert error.message == message
    assert str(error) == expected_str
    assert repr(error) == expected_repr
