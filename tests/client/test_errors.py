from typing import NoReturn

import pytest

from a2a.client import A2AClientError, A2AClientHTTPError, A2AClientJSONError


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


class TestExceptionHierarchy:
    """Test the exception hierarchy and relationships."""

    def test_exception_hierarchy(self) -> None:
        """Test that the exception hierarchy is correct."""
        assert issubclass(A2AClientError, Exception)
        assert issubclass(A2AClientHTTPError, A2AClientError)
        assert issubclass(A2AClientJSONError, A2AClientError)

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


# Additional parametrized tests for more comprehensive coverage


@pytest.mark.parametrize(
    'status_code,message,expected',
    [
        (400, 'Bad Request', 'HTTP Error 400: Bad Request'),
        (404, 'Not Found', 'HTTP Error 404: Not Found'),
        (500, 'Server Error', 'HTTP Error 500: Server Error'),
    ],
)
def test_http_error_parametrized(
    status_code: int, message: str, expected: str
) -> None:
    """Parametrized test for HTTP errors with different status codes."""
    error = A2AClientHTTPError(status_code, message)
    assert error.status_code == status_code
    assert error.message == message
    assert str(error) == expected


@pytest.mark.parametrize(
    'message,expected',
    [
        ('Missing field', 'JSON Error: Missing field'),
        ('Invalid type', 'JSON Error: Invalid type'),
        ('Parsing failed', 'JSON Error: Parsing failed'),
    ],
)
def test_json_error_parametrized(message: str, expected: str) -> None:
    """Parametrized test for JSON errors with different messages."""
    error = A2AClientJSONError(message)
    assert error.message == message
    assert str(error) == expected
