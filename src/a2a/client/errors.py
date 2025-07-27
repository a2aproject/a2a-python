"""Custom exceptions for the A2A client."""


class A2AClientError(Exception):
    """Base exception for A2A Client errors."""


class A2AClientHTTPError(A2AClientError):
    """Client exception for HTTP errors received from the server."""

    def __init__(self, status_code: int, message: str):
        """Initializes the A2AClientHTTPError.

        Args:
            status_code: The HTTP status code of the response.
            message: A descriptive error message.
        """
        self.status_code = status_code
        self.message = message
        super().__init__(f'HTTP Error {status_code}: {message}')


class A2AClientJSONError(A2AClientError):
    """Client exception for JSON errors during response parsing or validation."""

    def __init__(self, message: str):
        """Initializes the A2AClientJSONError.

        Args:
            message: A descriptive error message.
        """
        self.message = message
        super().__init__(f'JSON Error: {message}')


class A2AClientTimeoutError(A2AClientError):
    """Client exception for timeout errors during a request."""

    def __init__(self, message: str):
        """Initializes the A2AClientTimeoutError.

        Args:
            message: A descriptive error message.
        """
        self.message = message
        super().__init__(f'Timeout Error: {message}')
