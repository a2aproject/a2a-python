"""Helper functions for building A2A JSON-RPC responses."""

from typing import Any, cast, get_args

from google.protobuf.json_format import MessageToDict
from google.protobuf.message import Message as ProtoMessage
from jsonrpc.jsonrpc2 import JSONRPC20Response

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
from a2a.types.extras import (
    A2AError,
    InvalidAgentResponseError,
    JSONRPCError,
)


# Tuple of all A2AError types for isinstance checks
_A2A_ERROR_TYPES: tuple[type, ...] = get_args(A2AError)


# Result types for handler responses
EventTypes = (
    Task
    | Message
    | TaskArtifactUpdateEvent
    | TaskStatusUpdateEvent
    | TaskPushNotificationConfig
    | StreamResponse
    | SendMessageResponseProto
    | A2AError
    | JSONRPCError
    | list[TaskPushNotificationConfig]
)
"""Type alias for possible event types produced by handlers."""


def build_error_response(
    request_id: str | int | None,
    error: A2AError | JSONRPCError,
) -> dict[str, Any]:
    """Build a JSON-RPC error response dict.

    Args:
        request_id: The ID of the request that caused the error.
        error: The A2AError or JSONRPCError object.

    Returns:
        A dict representing the JSON-RPC error response.
    """
    error_dict = error.model_dump(exclude_none=True)
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
        return build_error_response(request_id, cast('A2AError', response))

    # If response is not an expected success type and not an error,
    # it's an invalid type of response from the agent for this method.
    error = InvalidAgentResponseError(
        message='Agent returned invalid type response for this method'
    )
    return build_error_response(request_id, error)
