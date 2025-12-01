# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""SDK-specific types that are not part of the A2A proto definition.

These types are used for JSON-RPC error handling and other SDK-specific
functionality that extends beyond the core A2A protocol types.

For JSON-RPC request/response handling, use the `jsonrpc` library:
    from jsonrpc.jsonrpc2 import JSONRPC20Request, JSONRPC20Response
"""

from typing import Any, Literal

from pydantic import BaseModel

from a2a.types.a2a_pb2 import (
    CancelTaskRequest,
    GetExtendedAgentCardRequest,
    GetTaskPushNotificationConfigRequest,
    GetTaskRequest,
    SendMessageRequest,
    SetTaskPushNotificationConfigRequest,
    SubscribeToTaskRequest,
)


# TaskResubscriptionRequest is an alias for SubscribeToTaskRequest
# (backwards compatibility)
TaskResubscriptionRequest = SubscribeToTaskRequest


# Transport protocol constants
# These match the protocol binding values used in AgentCard
class TransportProtocol:
    """Transport protocol string constants."""

    jsonrpc = 'JSONRPC'
    http_json = 'HTTP+JSON'
    grpc = 'GRPC'


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


# Type alias for A2A requests (union of all request types)
A2ARequest = (
    SendMessageRequest
    | GetTaskRequest
    | CancelTaskRequest
    | SetTaskPushNotificationConfigRequest
    | GetTaskPushNotificationConfigRequest
    | SubscribeToTaskRequest
    | GetExtendedAgentCardRequest
)


__all__ = [
    'A2ABaseModel',
    'A2AError',
    'A2ARequest',
    'AuthenticatedExtendedCardNotConfiguredError',
    'ContentTypeNotSupportedError',
    'InternalError',
    'InvalidAgentResponseError',
    'InvalidParamsError',
    'InvalidRequestError',
    'JSONParseError',
    'JSONRPCError',
    'MethodNotFoundError',
    'PushNotificationNotSupportedError',
    'TaskNotCancelableError',
    'TaskNotFoundError',
    'TaskResubscriptionRequest',
    'TransportProtocol',
    'UnsupportedOperationError',
]
