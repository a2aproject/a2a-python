"""Custom exceptions and error types for A2A server-side errors.

This module contains JSON-RPC error types and A2A-specific error codes,
as well as server exception classes.
"""

from typing import Any, Literal

from pydantic import BaseModel


class A2ABaseModel(BaseModel):
    """Base model for all A2A SDK types."""

    model_config = {
        'extra': 'allow',
        'populate_by_name': True,
        'arbitrary_types_allowed': True,
    }


# JSON-RPC Error types - A2A specific error codes
class JSONRPCError(A2ABaseModel):
    """Represents a JSON-RPC 2.0 Error object."""

    code: int
    """A number that indicates the error type that occurred."""
    message: str
    """A string providing a short description of the error."""
    data: Any | None = None
    """Additional information about the error."""


class JSONParseError(A2ABaseModel):
    """JSON-RPC parse error (-32700)."""

    code: Literal[-32700] = -32700
    message: str = 'Parse error'
    data: Any | None = None


class InvalidRequestError(A2ABaseModel):
    """JSON-RPC invalid request error (-32600)."""

    code: Literal[-32600] = -32600
    message: str = 'Invalid Request'
    data: Any | None = None


class MethodNotFoundError(A2ABaseModel):
    """JSON-RPC method not found error (-32601)."""

    code: Literal[-32601] = -32601
    message: str = 'Method not found'
    data: Any | None = None


class InvalidParamsError(A2ABaseModel):
    """JSON-RPC invalid params error (-32602)."""

    code: Literal[-32602] = -32602
    message: str = 'Invalid params'
    data: Any | None = None


class InternalError(A2ABaseModel):
    """JSON-RPC internal error (-32603)."""

    code: Literal[-32603] = -32603
    message: str = 'Internal error'
    data: Any | None = None


class TaskNotFoundError(A2ABaseModel):
    """A2A-specific error for task not found (-32001)."""

    code: Literal[-32001] = -32001
    message: str = 'Task not found'
    data: Any | None = None


class TaskNotCancelableError(A2ABaseModel):
    """A2A-specific error for task not cancelable (-32002)."""

    code: Literal[-32002] = -32002
    message: str = 'Task cannot be canceled'
    data: Any | None = None


class PushNotificationNotSupportedError(A2ABaseModel):
    """A2A-specific error for push notification not supported (-32003)."""

    code: Literal[-32003] = -32003
    message: str = 'Push Notification is not supported'
    data: Any | None = None


class UnsupportedOperationError(A2ABaseModel):
    """A2A-specific error for unsupported operation (-32004)."""

    code: Literal[-32004] = -32004
    message: str = 'This operation is not supported'
    data: Any | None = None


class ContentTypeNotSupportedError(A2ABaseModel):
    """A2A-specific error for content type not supported (-32005)."""

    code: Literal[-32005] = -32005
    message: str = 'Incompatible content types'
    data: Any | None = None


class InvalidAgentResponseError(A2ABaseModel):
    """A2A-specific error for invalid agent response (-32006)."""

    code: Literal[-32006] = -32006
    message: str = 'Invalid agent response'
    data: Any | None = None


class AuthenticatedExtendedCardNotConfiguredError(A2ABaseModel):
    """A2A-specific error for authenticated extended card not configured (-32007)."""

    code: Literal[-32007] = -32007
    message: str = 'Authenticated Extended Card is not configured'
    data: Any | None = None


# Union of all A2A error types
A2AError = (
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
    | AuthenticatedExtendedCardNotConfiguredError
)


__all__ = [
    'A2ABaseModel',
    'A2AError',
    'A2AServerError',
    'AuthenticatedExtendedCardNotConfiguredError',
    'ContentTypeNotSupportedError',
    'InternalError',
    'InvalidAgentResponseError',
    'InvalidParamsError',
    'InvalidRequestError',
    'JSONParseError',
    'JSONRPCError',
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
    """Wrapper exception for A2A or JSON-RPC errors originating from the server's logic.

    This exception is used internally by request handlers and other server components
    to signal a specific error that should be formatted as a JSON-RPC error response.
    """

    def __init__(
        self,
        error: A2AError | None,
    ):
        """Initializes the ServerError.

        Args:
            error: The specific A2A or JSON-RPC error model instance.
        """
        self.error = error

    def __str__(self) -> str:
        """Returns a readable representation of the internal error."""
        if self.error is None:
            return 'None'
        if self.error.message is None:
            return self.error.__class__.__name__
        return self.error.message

    def __repr__(self) -> str:
        """Returns an unambiguous representation for developers showing how the ServerError was constructed with the internal error."""
        return f'{self.__class__.__name__}({self.error!r})'
