"""Custom exceptions and error types for A2A server-side errors.

This module contains A2A-specific error codes,
as well as server exception classes.
"""


class A2AError(Exception):
    """Base exception for A2A errors."""

    message: str = 'A2A Error'

    def __init__(self, message: str | None = None):
        if message:
            self.message = message
        super().__init__(self.message)


class TaskNotFoundError(A2AError):
    """Exception raised when a task is not found."""

    message = 'Task not found'


class TaskNotCancelableError(A2AError):
    """Exception raised when a task cannot be canceled."""

    message = 'Task cannot be canceled'


class PushNotificationNotSupportedError(A2AError):
    """Exception raised when push notifications are not supported."""

    message = 'Push Notification is not supported'


class UnsupportedOperationError(A2AError):
    """Exception raised when an operation is not supported."""

    message = 'This operation is not supported'


class ContentTypeNotSupportedError(A2AError):
    """Exception raised when the content type is incompatible."""

    message = 'Incompatible content types'


class InternalError(A2AError):
    """Exception raised for internal server errors."""

    message = 'Internal error'


class InvalidAgentResponseError(A2AError):
    """Exception raised when the agent response is invalid."""

    message = 'Invalid agent response'


class AuthenticatedExtendedCardNotConfiguredError(A2AError):
    """Exception raised when the authenticated extended card is not configured."""

    message = 'Authenticated Extended Card is not configured'


class InvalidParamsError(A2AError):
    """Exception raised when parameters are invalid."""

    message = 'Invalid params'


class InvalidRequestError(A2AError):
    """Exception raised when the request is invalid."""

    message = 'Invalid Request'


class MethodNotFoundError(A2AError):
    """Exception raised when a method is not found."""

    message = 'Method not found'


class ExtensionSupportRequiredError(A2AError):
    """Exception raised when extension support is required but not present."""

    message = 'Extension support required'


class VersionNotSupportedError(A2AError):
    """Exception raised when the requested version is not supported."""

    message = 'Version not supported'


# For backward compatibility if needed, or just aliases for clean refactor
# We remove the Pydantic models here.

__all__ = [
    'A2A_ERROR_REASONS',
    'A2A_REASON_TO_ERROR',
    'JSON_RPC_ERROR_CODE_MAP',
    'ExtensionSupportRequiredError',
    'InternalError',
    'InvalidAgentResponseError',
    'InvalidParamsError',
    'InvalidRequestError',
    'MethodNotFoundError',
    'PushNotificationNotSupportedError',
    'TaskNotCancelableError',
    'TaskNotFoundError',
    'UnsupportedOperationError',
    'VersionNotSupportedError',
]


JSON_RPC_ERROR_CODE_MAP: dict[type[A2AError], int] = {
    TaskNotFoundError: -32001,
    TaskNotCancelableError: -32002,
    PushNotificationNotSupportedError: -32003,
    UnsupportedOperationError: -32004,
    ContentTypeNotSupportedError: -32005,
    InvalidAgentResponseError: -32006,
    AuthenticatedExtendedCardNotConfiguredError: -32007,
    InvalidParamsError: -32602,
    InvalidRequestError: -32600,
    MethodNotFoundError: -32601,
    InternalError: -32603,
}


A2A_ERROR_REASONS = {
    TaskNotFoundError: 'TASK_NOT_FOUND',
    TaskNotCancelableError: 'TASK_NOT_CANCELABLE',
    PushNotificationNotSupportedError: 'PUSH_NOTIFICATION_NOT_SUPPORTED',
    UnsupportedOperationError: 'UNSUPPORTED_OPERATION',
    ContentTypeNotSupportedError: 'CONTENT_TYPE_NOT_SUPPORTED',
    InvalidAgentResponseError: 'INVALID_AGENT_RESPONSE',
    AuthenticatedExtendedCardNotConfiguredError: 'EXTENDED_AGENT_CARD_NOT_CONFIGURED',
    ExtensionSupportRequiredError: 'EXTENSION_SUPPORT_REQUIRED',
    VersionNotSupportedError: 'VERSION_NOT_SUPPORTED',
    InvalidParamsError: 'INVALID_PARAMS',
    InvalidRequestError: 'INVALID_REQUEST',
    MethodNotFoundError: 'METHOD_NOT_FOUND',
    InternalError: 'INTERNAL_ERROR',
}

A2A_REASON_TO_ERROR = {reason: cls for cls, reason in A2A_ERROR_REASONS.items()}
