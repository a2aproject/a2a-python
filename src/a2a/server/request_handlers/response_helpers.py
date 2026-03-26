"""Helper functions for building A2A JSON-RPC responses."""

from typing import Any

from google.protobuf.json_format import MessageToDict
from google.protobuf.message import Message as ProtoMessage
from jsonrpc.jsonrpc2 import JSONRPC20Response

from a2a.compat.v0_3.conversions import to_compat_agent_card
from a2a.server.jsonrpc_models import (
    InternalError as JSONRPCInternalError,
)
from a2a.server.jsonrpc_models import (
    JSONRPCError,
)
from a2a.types.a2a_pb2 import (
    AgentCard,
    ListTasksResponse,
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
    JSON_RPC_ERROR_CODE_MAP,
    A2AError,
    ContentTypeNotSupportedError,
    ExtendedAgentCardNotConfiguredError,
    ExtensionSupportRequiredError,
    InternalError,
    InvalidAgentResponseError,
    InvalidParamsError,
    InvalidRequestError,
    MethodNotFoundError,
    PushNotificationNotSupportedError,
    TaskNotCancelableError,
    TaskNotFoundError,
    UnsupportedOperationError,
    VersionNotSupportedError,
)


EXCEPTION_MAP: dict[type[A2AError], type[JSONRPCError]] = {
    TaskNotFoundError: JSONRPCError,
    TaskNotCancelableError: JSONRPCError,
    PushNotificationNotSupportedError: JSONRPCError,
    UnsupportedOperationError: JSONRPCError,
    ContentTypeNotSupportedError: JSONRPCError,
    InvalidAgentResponseError: JSONRPCError,
    ExtendedAgentCardNotConfiguredError: JSONRPCError,
    InvalidParamsError: JSONRPCError,
    InvalidRequestError: JSONRPCError,
    MethodNotFoundError: JSONRPCError,
    InternalError: JSONRPCInternalError,
    ExtensionSupportRequiredError: JSONRPCError,
    VersionNotSupportedError: JSONRPCError,
}


# Tuple of all A2AError types for isinstance checks
_A2A_ERROR_TYPES: tuple[type, ...] = (A2AError,)


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
    | ListTasksResponse
)
"""Type alias for possible event types produced by handlers."""


def agent_card_to_dict(card: AgentCard) -> dict[str, Any]:
    """Convert AgentCard to dict and inject backward compatibility fields."""
    result = MessageToDict(card)

    try:
        compat_card = to_compat_agent_card(card)
        compat_dict = compat_card.model_dump(exclude_none=True)
    except VersionNotSupportedError:
        compat_dict = {}

    # Do not include supportsAuthenticatedExtendedCard if false
    if not compat_dict.get('supportsAuthenticatedExtendedCard'):
        compat_dict.pop('supportsAuthenticatedExtendedCard', None)

    def merge(dict1: dict[str, Any], dict2: dict[str, Any]) -> dict[str, Any]:
        for k, v in dict2.items():
            if k not in dict1:
                dict1[k] = v
            elif isinstance(v, dict) and isinstance(dict1[k], dict):
                merge(dict1[k], v)
            elif isinstance(v, list) and isinstance(dict1[k], list):
                for i in range(min(len(dict1[k]), len(v))):
                    if isinstance(dict1[k][i], dict) and isinstance(v[i], dict):
                        merge(dict1[k][i], v[i])
        return dict1

    return merge(result, compat_dict)


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
    jsonrpc_error: JSONRPCError
    if isinstance(error, JSONRPCError):
        jsonrpc_error = error
    elif isinstance(error, A2AError):
        error_type = type(error)
        model_class = EXCEPTION_MAP.get(error_type, JSONRPCInternalError)
        code = JSON_RPC_ERROR_CODE_MAP.get(error_type, -32603)
        jsonrpc_error = model_class(
            code=code,
            message=str(error),
            data=error.data,
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

    if isinstance(response, A2AError | JSONRPCError):
        return build_error_response(request_id, response)

    # If response is not an expected success type and not an error,
    # it's an invalid type of response from the agent for this method.
    error = InvalidAgentResponseError(
        message='Agent returned invalid type response for this method'
    )
    return build_error_response(request_id, error)
