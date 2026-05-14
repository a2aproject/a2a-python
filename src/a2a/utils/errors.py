"""Custom exceptions and error types for A2A server-side errors.

This module contains A2A-specific error codes,
as well as server exception classes.
"""

from typing import NamedTuple


class ErrorMapping(NamedTuple):
    """Named tuple mapping HTTP status, gRPC status, and reason strings."""

    http_code: int
    grpc_status: str
    reason: str


class A2AError(Exception):
    """Base exception for A2A errors."""

    message: str = 'A2A Error'
    data: dict | None = None

    def __init__(self, message: str | None = None, data: dict | None = None):
        if message:
            self.message = message
        self.data = data
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


class ExtendedAgentCardNotConfiguredError(A2AError):
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


class JSONParseError(A2AError):
    """Exception raised when invalid JSON was received by the server."""

    message = 'Invalid JSON payload'


class ExtensionSupportRequiredError(A2AError):
    """Exception raised when extension support is required but not present."""

    message = 'Extension support required'


class VersionNotSupportedError(A2AError):
    """Exception raised when the requested version is not supported."""

    message = 'Version not supported'


__all__ = [
    'A2A_ERROR_MAPPING',
    'A2A_ERROR_REASONS',
    'A2A_REASON_TO_ERROR',
    'A2A_REST_ERROR_MAPPING',
    'JSON_RPC_ERROR_CODE_MAP',
    'ErrorMapping',
    'ExtensionSupportRequiredError',
    'InternalError',
    'InvalidAgentResponseError',
    'InvalidParamsError',
    'InvalidRequestError',
    'JSONParseError',
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
    ExtendedAgentCardNotConfiguredError: -32007,
    ExtensionSupportRequiredError: -32008,
    VersionNotSupportedError: -32009,
    InvalidParamsError: -32602,
    InvalidRequestError: -32600,
    MethodNotFoundError: -32601,
    InternalError: -32603,
    JSONParseError: -32700,
}


A2A_ERROR_MAPPING: dict[type[A2AError], ErrorMapping] = {
    TaskNotFoundError: ErrorMapping(404, 'NOT_FOUND', 'TASK_NOT_FOUND'),
    TaskNotCancelableError: ErrorMapping(
        400, 'FAILED_PRECONDITION', 'TASK_NOT_CANCELABLE'
    ),
    PushNotificationNotSupportedError: ErrorMapping(
        400,
        'FAILED_PRECONDITION',
        'PUSH_NOTIFICATION_NOT_SUPPORTED',
    ),
    UnsupportedOperationError: ErrorMapping(
        400, 'FAILED_PRECONDITION', 'UNSUPPORTED_OPERATION'
    ),
    ContentTypeNotSupportedError: ErrorMapping(
        400,
        'INVALID_ARGUMENT',
        'CONTENT_TYPE_NOT_SUPPORTED',
    ),
    InvalidAgentResponseError: ErrorMapping(
        500, 'INTERNAL', 'INVALID_AGENT_RESPONSE'
    ),
    ExtendedAgentCardNotConfiguredError: ErrorMapping(
        400,
        'FAILED_PRECONDITION',
        'EXTENDED_AGENT_CARD_NOT_CONFIGURED',
    ),
    ExtensionSupportRequiredError: ErrorMapping(
        400,
        'FAILED_PRECONDITION',
        'EXTENSION_SUPPORT_REQUIRED',
    ),
    VersionNotSupportedError: ErrorMapping(
        400, 'FAILED_PRECONDITION', 'VERSION_NOT_SUPPORTED'
    ),
    InvalidParamsError: ErrorMapping(400, 'INVALID_ARGUMENT', 'INVALID_PARAMS'),
    InvalidRequestError: ErrorMapping(
        400, 'INVALID_ARGUMENT', 'INVALID_REQUEST'
    ),
    MethodNotFoundError: ErrorMapping(404, 'NOT_FOUND', 'METHOD_NOT_FOUND'),
    InternalError: ErrorMapping(500, 'INTERNAL', 'INTERNAL_ERROR'),
}


# Deprecated alias kept for backwards compatibility; remove in the next
# major version.
A2A_REST_ERROR_MAPPING = A2A_ERROR_MAPPING


A2A_ERROR_REASONS = {
    cls: mapping.reason for cls, mapping in A2A_ERROR_MAPPING.items()
}

A2A_REASON_TO_ERROR = {
    mapping.reason: cls for cls, mapping in A2A_ERROR_MAPPING.items()
}
