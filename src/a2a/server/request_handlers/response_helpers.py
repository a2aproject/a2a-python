"""Helper functions for building A2A JSON-RPC responses."""

# response types
from typing import TypeVar

from a2a.types import (
    A2AError,
    CancelTaskResponse,
    CancelTaskSuccessResponse,
    DeleteTaskPushNotificationConfigResponse,
    DeleteTaskPushNotificationConfigSuccessResponse,
    GetTaskPushNotificationConfigResponse,
    GetTaskPushNotificationConfigSuccessResponse,
    GetTaskResponse,
    GetTaskSuccessResponse,
    InvalidAgentResponseError,
    JSONRPCError,
    JSONRPCErrorResponse,
    ListTaskPushNotificationConfigResponse,
    ListTaskPushNotificationConfigSuccessResponse,
    Message,
    SendMessageResponse,
    SendMessageSuccessResponse,
    SendStreamingMessageResponse,
    SendStreamingMessageSuccessResponse,
    SetTaskPushNotificationConfigResponse,
    SetTaskPushNotificationConfigSuccessResponse,
    Task,
    TaskArtifactUpdateEvent,
    TaskPushNotificationConfig,
    TaskStatusUpdateEvent,
)


RT = TypeVar(
    'RT',
    GetTaskResponse,
    CancelTaskResponse,
    SendMessageResponse,
    SetTaskPushNotificationConfigResponse,
    GetTaskPushNotificationConfigResponse,
    SendStreamingMessageResponse,
    ListTaskPushNotificationConfigResponse,
    DeleteTaskPushNotificationConfigResponse,
)
"""Type variable for RootModel response types."""

# success types
SPT = TypeVar(
    'SPT',
    GetTaskSuccessResponse,
    CancelTaskSuccessResponse,
    SendMessageSuccessResponse,
    SetTaskPushNotificationConfigSuccessResponse,
    GetTaskPushNotificationConfigSuccessResponse,
    SendStreamingMessageSuccessResponse,
    ListTaskPushNotificationConfigSuccessResponse,
    DeleteTaskPushNotificationConfigSuccessResponse,
)
"""Type variable for SuccessResponse types."""

# result types
EventTypes = (
    Task
    | Message
    | TaskArtifactUpdateEvent
    | TaskStatusUpdateEvent
    | TaskPushNotificationConfig
    | A2AError
    | JSONRPCError
    | list[TaskPushNotificationConfig]
)
"""Type alias for possible event types produced by handlers."""


def build_error_response(
    request_id: str | int | None,
    error: A2AError | JSONRPCError,
    response_wrapper_type: type[RT],
) -> RT:
    """Helper method to build a JSONRPCErrorResponse wrapped in the appropriate response type.

    Args:
        request_id: The ID of the request that caused the error.
        error: The A2AError or JSONRPCError object.
        response_wrapper_type: The Pydantic RootModel type that wraps the response
                                for the specific RPC method (e.g., `SendMessageResponse`).

    Returns:
        A Pydantic model representing the JSON-RPC error response,
        wrapped in the specified response type.
    """
    return response_wrapper_type(
        JSONRPCErrorResponse(
            id=request_id,
            error=error.root if isinstance(error, A2AError) else error,
        )
    )


def prepare_response_object(
    request_id: str | int | None,
    response: EventTypes,
    success_response_types: tuple[type, ...],
    success_payload_type: type[SPT],
    response_type: type[RT],
) -> RT:
    """Helper method to build appropriate JSONRPCResponse object for RPC methods.

    Based on the type of the `response` object received from the handler,
    it constructs either a success response wrapped in the appropriate payload type
    or an error response.

    Args:
        request_id: The ID of the request.
        response: The object received from the request handler.
        success_response_types: A tuple of expected Pydantic model types for a successful result.
        success_payload_type: The Pydantic model type for the success payload
                                (e.g., `SendMessageSuccessResponse`).
        response_type: The Pydantic RootModel type that wraps the final response
                       (e.g., `SendMessageResponse`).

    Returns:
        A Pydantic model representing the final JSON-RPC response (success or error).
    """
    if isinstance(response, success_response_types):
        return response_type(
            root=success_payload_type(id=request_id, result=response)  # type:ignore
        )

    if isinstance(response, A2AError | JSONRPCError):
        return build_error_response(request_id, response, response_type)

    # If consumer_data is not an expected success type and not an error,
    # it's an invalid type of response from the agent for this specific method.
    response = A2AError(
        root=InvalidAgentResponseError(
            message='Agent returned invalid type response for this method'
        )
    )

    return build_error_response(request_id, response, response_type)
