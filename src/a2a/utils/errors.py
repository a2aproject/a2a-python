"""Custom exceptions for A2A server-side errors."""

from a2a.types import (
    A2AError,
    ContentTypeNotSupportedError,
    InternalError,
    InvalidAgentResponseError,
    InvalidParamsError,
    InvalidRequestError,
    JSONParseError,
    JSONRPCError,
    MethodNotFoundError,
    PushNotificationNotSupportedError,
    TaskNotCancelableError,
    TaskNotFoundError,
    UnsupportedOperationError,
)


class A2AServerError(Exception):
    """Base exception for A2A Server errors."""


class MethodNotImplementedError(A2AServerError):
    """Exception raised for methods that are not implemented by the server handler."""

    def __init__(
        self, message: str = 'This method is not implemented by the server'
    ):
        """Initializes the MethodNotImplementedError.

        Args:
            message: A descriptive error message.
        """
        self.message = message
        super().__init__(f'Not Implemented operation Error: {message}')


class A2AErrorWrapperError(Exception):
    """Wrapper exception for A2A that is discriminated union of all standard JSON-RPC and A2A-specific error types."""

    def __init__(
        self,
        error: (A2AError),
    ):
        """Initialize the A2AError wrapper.

        Args:
            error: The specific A2AError model instance.
        """
        self.error = error


class ServerError(Exception):
    """Wrapper exception for A2A or JSON-RPC errors originating from the server's logic.

    This exception is used internally by request handlers and other server components
    to signal a specific error that should be formatted as a JSON-RPC error response.
    """

    def __init__(
        self,
        error: (
            JSONRPCError
            | JSONParseError
            | InvalidRequestError
            | MethodNotFoundError
            | InvalidParamsError
            | InternalError
            | TaskNotFoundError
            | TaskNotCancelableError
            | PushNotificationNotSupportedError
            | UnsupportedOperationError
            | ContentTypeNotSupportedError
            | InvalidAgentResponseError
            | None
        ),
    ):
        """Initializes the ServerError.

        Args:
            error: The specific A2A or JSON-RPC error model instance.
                   If None, an `InternalError` will be used when formatting the response.
        """
        self.error = error
