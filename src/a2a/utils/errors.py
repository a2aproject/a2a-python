"""Custom exceptions and error types for A2A server-side errors.

This module contains A2A-specific error codes,
as well as server exception classes.
"""


class A2AException(Exception):
    """Base exception for A2A errors."""

    message: str = 'A2A Error'

    def __init__(self, message: str | None = None):
        if message:
            self.message = message
        super().__init__(self.message)


class TaskNotFoundError(A2AException):
    message = 'Task not found'


class TaskNotCancelableError(A2AException):
    message = 'Task cannot be canceled'


class PushNotificationNotSupportedError(A2AException):
    message = 'Push Notification is not supported'


class UnsupportedOperationError(A2AException):
    message = 'This operation is not supported'


class ContentTypeNotSupportedError(A2AException):
    message = 'Incompatible content types'


class InternalError(A2AException):
    message = 'Internal error'


class InvalidAgentResponseError(A2AException):
    message = 'Invalid agent response'


class AuthenticatedExtendedCardNotConfiguredError(A2AException):
    message = 'Authenticated Extended Card is not configured'


class InvalidParamsError(A2AException):
    message = 'Invalid params'


class InvalidRequestError(A2AException):
    message = 'Invalid Request'


class MethodNotFoundError(A2AException):
    message = 'Method not found'


# For backward compatibility if needed, or just aliases for clean refactor
# We remove the Pydantic models here.

__all__ = [
    'A2AException',
    'A2AServerError',
    'AuthenticatedExtendedCardNotConfiguredError',
    'ContentTypeNotSupportedError',
    'InternalError',
    'InvalidAgentResponseError',
    'InvalidParamsError',
    'InvalidRequestError',
    'MethodNotFoundError',
    'MethodNotImplementedError',
    'PushNotificationNotSupportedError',
    'ServerError',
    'TaskNotCancelableError',
    'TaskNotFoundError',
    'UnsupportedOperationError',
]


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


class ServerError(Exception):
    """Wrapper exception for A2A errors originating from the server's logic.

    This exception is used internally by request handlers and other server components
    to signal a specific error.
    """

    def __init__(
        self,
        error: Exception | None,
    ):
        """Initializes the ServerError.

        Args:
            error: The specific A2A exception.
        """
        self.error = error

    def __str__(self) -> str:
        """Returns a readable representation of the internal error."""
        if self.error is None:
            return 'None'
        return str(self.error)

    def __repr__(self) -> str:
        """Returns an unambiguous representation for developers showing how the ServerError was constructed with the internal error."""
        return f'{self.__class__.__name__}({self.error!r})'
