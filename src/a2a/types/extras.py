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

These types are used for JSON-RPC handling, error responses, and other
SDK-specific functionality that extends beyond the core A2A protocol types.
"""

from typing import Any, Literal

from pydantic import BaseModel, RootModel

from a2a.types.a2a_pb2 import (
    CancelTaskRequest,
    GetExtendedAgentCardRequest,
    GetTaskPushNotificationConfigRequest,
    GetTaskRequest,
    SendMessageRequest,
    SetTaskPushNotificationConfigRequest,
    SubscribeToTaskRequest,
)


# Alias for backward compatibility - SubscribeToTaskRequest was previously named
# TaskResubscriptionRequest in the Pydantic types
TaskResubscriptionRequest = SubscribeToTaskRequest


# Transport protocol constants for backward compatibility
# These were an enum in the old Pydantic types, now they're just strings
class TransportProtocol:
    """Transport protocol string constants for backward compatibility."""

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


# JSON-RPC Error types
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


class JSONRPCRequest(A2ABaseModel):
    """Represents a JSON-RPC 2.0 Request object."""

    jsonrpc: Literal['2.0'] = '2.0'
    method: str
    params: Any | None = None
    id: str | int | None = None


class JSONRPCResponse(A2ABaseModel):
    """Represents a JSON-RPC 2.0 Success Response object."""

    jsonrpc: Literal['2.0'] = '2.0'
    result: Any
    id: str | int | None = None


class JSONRPCErrorResponse(A2ABaseModel):
    """Represents a JSON-RPC 2.0 Error Response object."""

    jsonrpc: Literal['2.0'] = '2.0'
    error: A2AError
    id: str | int | None = None


# Type alias for A2A requests (union of all request types)
# This maps to the various request message types in the proto
A2ARequest = (
    SendMessageRequest
    | GetTaskRequest
    | CancelTaskRequest
    | SetTaskPushNotificationConfigRequest
    | GetTaskPushNotificationConfigRequest
    | SubscribeToTaskRequest
    | GetExtendedAgentCardRequest
)


# JSON-RPC Success Response types
# These wrap the result of successful RPC calls
# Note: result is typed as Any to allow both proto messages and dicts
class GetTaskSuccessResponse(A2ABaseModel):
    """Success response for GetTask RPC."""

    jsonrpc: Literal['2.0'] = '2.0'
    id: str | int | None = None
    result: Any


class CancelTaskSuccessResponse(A2ABaseModel):
    """Success response for CancelTask RPC."""

    jsonrpc: Literal['2.0'] = '2.0'
    id: str | int | None = None
    result: Any


class SendMessageSuccessResponse(A2ABaseModel):
    """Success response for SendMessage RPC."""

    jsonrpc: Literal['2.0'] = '2.0'
    id: str | int | None = None
    result: Any


class SendStreamingMessageSuccessResponse(A2ABaseModel):
    """Success response for streaming message RPC."""

    jsonrpc: Literal['2.0'] = '2.0'
    id: str | int | None = None
    result: Any  # Streaming events


class SetTaskPushNotificationConfigSuccessResponse(A2ABaseModel):
    """Success response for SetTaskPushNotificationConfig RPC."""

    jsonrpc: Literal['2.0'] = '2.0'
    id: str | int | None = None
    result: Any


class GetTaskPushNotificationConfigSuccessResponse(A2ABaseModel):
    """Success response for GetTaskPushNotificationConfig RPC."""

    jsonrpc: Literal['2.0'] = '2.0'
    id: str | int | None = None
    result: Any


class ListTaskPushNotificationConfigSuccessResponse(A2ABaseModel):
    """Success response for ListTaskPushNotificationConfig RPC."""

    jsonrpc: Literal['2.0'] = '2.0'
    id: str | int | None = None
    result: Any


class DeleteTaskPushNotificationConfigSuccessResponse(A2ABaseModel):
    """Success response for DeleteTaskPushNotificationConfig RPC."""

    jsonrpc: Literal['2.0'] = '2.0'
    id: str | int | None = None
    result: None = None


class GetAuthenticatedExtendedCardSuccessResponse(A2ABaseModel):
    """Success response for GetAuthenticatedExtendedCard RPC."""

    jsonrpc: Literal['2.0'] = '2.0'
    id: str | int | None = None
    result: Any  # AgentCard


# JSON-RPC Response RootModel types
# These are union types that can be either success or error
GetTaskResponse = RootModel[GetTaskSuccessResponse | JSONRPCErrorResponse]
CancelTaskResponse = RootModel[CancelTaskSuccessResponse | JSONRPCErrorResponse]
SendMessageResponse = RootModel[
    SendMessageSuccessResponse | JSONRPCErrorResponse
]
SendStreamingMessageResponse = RootModel[
    SendStreamingMessageSuccessResponse | JSONRPCErrorResponse
]
SetTaskPushNotificationConfigResponse = RootModel[
    SetTaskPushNotificationConfigSuccessResponse | JSONRPCErrorResponse
]
GetTaskPushNotificationConfigResponse = RootModel[
    GetTaskPushNotificationConfigSuccessResponse | JSONRPCErrorResponse
]
ListTaskPushNotificationConfigResponse = RootModel[
    ListTaskPushNotificationConfigSuccessResponse | JSONRPCErrorResponse
]
DeleteTaskPushNotificationConfigResponse = RootModel[
    DeleteTaskPushNotificationConfigSuccessResponse | JSONRPCErrorResponse
]
GetAuthenticatedExtendedCardResponse = RootModel[
    GetAuthenticatedExtendedCardSuccessResponse | JSONRPCErrorResponse
]


__all__ = [
    'A2AError',
    'A2ARequest',
    'AuthenticatedExtendedCardNotConfiguredError',
    'CancelTaskResponse',
    'CancelTaskSuccessResponse',
    'ContentTypeNotSupportedError',
    'DeleteTaskPushNotificationConfigResponse',
    'DeleteTaskPushNotificationConfigSuccessResponse',
    'GetAuthenticatedExtendedCardResponse',
    'GetAuthenticatedExtendedCardSuccessResponse',
    'GetTaskPushNotificationConfigResponse',
    'GetTaskPushNotificationConfigSuccessResponse',
    'GetTaskResponse',
    'GetTaskSuccessResponse',
    'InternalError',
    'InvalidAgentResponseError',
    'InvalidParamsError',
    'InvalidRequestError',
    'JSONParseError',
    'JSONRPCError',
    'JSONRPCErrorResponse',
    'JSONRPCRequest',
    'JSONRPCResponse',
    'ListTaskPushNotificationConfigResponse',
    'ListTaskPushNotificationConfigSuccessResponse',
    'MethodNotFoundError',
    'PushNotificationNotSupportedError',
    'SendMessageRequest',
    'SendMessageResponse',
    'SendMessageSuccessResponse',
    'SendStreamingMessageResponse',
    'SendStreamingMessageSuccessResponse',
    'SetTaskPushNotificationConfigResponse',
    'SetTaskPushNotificationConfigSuccessResponse',
    'TaskNotCancelableError',
    'TaskNotFoundError',
    'TaskResubscriptionRequest',
    'TransportProtocol',
    'UnsupportedOperationError',
]
