"""Helper functions for building A2A JSON-RPC responses."""

from typing import Any

from google.protobuf.json_format import MessageToDict
from google.protobuf.message import Message as ProtoMessage
from jsonrpc.jsonrpc2 import JSONRPC20Response

from a2a.server.apps.jsonrpc.errors import (
    InternalError as JSONRPCInternalError,
)
from a2a.server.apps.jsonrpc.errors import (
    JSONRPCError,
)
from a2a.types.a2a_pb2 import (
    Message,
    StreamResponse,
    Task,
    TaskArtifactUpdateEvent,
    TaskPushNotificationConfig,
    TaskStatusUpdateEvent,
)
from a2a.types.a2a_pb2 import (
    SendMessageResponse as SendMessageResponseProto,
)
from a2a.utils.errors import (
    A2AException,
    AuthenticatedExtendedCardNotConfiguredError,
    ContentTypeNotSupportedError,
    InternalError,
    InvalidAgentResponseError,
    InvalidParamsError,
    InvalidRequestError,
    MethodNotFoundError,
    PushNotificationNotSupportedError,
    TaskNotCancelableError,
    TaskNotFoundError,
    UnsupportedOperationError,
)


EXCEPTION_MAP: dict[type[A2AException], type[JSONRPCError]] = {
    TaskNotFoundError: JSONRPCError,
    TaskNotCancelableError: JSONRPCError,
    PushNotificationNotSupportedError: JSONRPCError,
    UnsupportedOperationError: JSONRPCError,
    ContentTypeNotSupportedError: JSONRPCError,
    InvalidAgentResponseError: JSONRPCError,
    AuthenticatedExtendedCardNotConfiguredError: JSONRPCError,
    InvalidParamsError: JSONRPCError,
    InvalidRequestError: JSONRPCError,
    MethodNotFoundError: JSONRPCError,
    InternalError: JSONRPCInternalError,
}

ERROR_CODE_MAP: dict[type[A2AException], int] = {
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
}


# Tuple of all A2AError types for isinstance checks
_A2A_ERROR_TYPES: tuple[type, ...] = (A2AException,)


# Result types for handler responses
EventTypes = (
    Task
    | Message
    | TaskArtifactUpdateEvent
    | TaskStatusUpdateEvent
    | TaskPushNotificationConfig
    | StreamResponse
    | SendMessageResponseProto
    | A2AException
    | JSONRPCError
    | list[TaskPushNotificationConfig]
)
"""Type alias for possible event types produced by handlers."""


def build_error_response(
    request_id: str | int | None,
    error: A2AException | JSONRPCError,
) -> dict[str, Any]:
    """Build a JSON-RPC error response dict.

    Args:
        request_id: The ID of the request that caused the error.
        error: The A2AException or JSONRPCError object.

    Returns:
        A dict representing the JSON-RPC error response.
    """
    jsonrpc_error: JSONRPCError
    if isinstance(error, JSONRPCError):
        jsonrpc_error = error
    elif isinstance(error, A2AException):
        error_type = type(error)
        model_class = EXCEPTION_MAP.get(error_type, JSONRPCInternalError)
        code = ERROR_CODE_MAP.get(error_type, -32603)
        jsonrpc_error = model_class(
            code=code,
            message=str(error),
        )
    else:
        jsonrpc_error = JSONRPCInternalError(message=str(error))

    error_dict = jsonrpc_error.model_dump(exclude_none=True)
    return JSONRPC20Response(error=error_dict, _id=request_id).data


def prepare_response_object(
    request_id: str | int | None,
    response: EventTypes,
    success_response_types: tuple[type, ...],
) -> dict[str, Any]:
    """Build a JSON-RPC response dict from handler output.

    Based on the type of the `response` object received from the handler,
    it constructs either a success response or an error response.

    Args:
        request_id: The ID of the request.
        response: The object received from the request handler.
        success_response_types: A tuple of expected types for a successful result.

    Returns:
        A dict representing the JSON-RPC response (success or error).
    """
    if isinstance(response, success_response_types):
        # Convert proto message to dict for JSON serialization
        result: Any = response
        if isinstance(response, ProtoMessage):
            result = MessageToDict(response, preserving_proto_field_name=False)
        return JSONRPC20Response(result=result, _id=request_id).data

    if isinstance(response, _A2A_ERROR_TYPES):
        return build_error_response(request_id, response)

    # If response is not an expected success type and not an error,
    # it's an invalid type of response from the agent for this method.
    error = InvalidAgentResponseError(
        message='Agent returned invalid type response for this method'
    )
    return build_error_response(request_id, error)
