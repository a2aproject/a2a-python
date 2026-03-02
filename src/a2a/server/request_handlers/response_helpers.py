"""Helper functions for building A2A JSON-RPC responses."""

from typing import Any

from google.protobuf.json_format import MessageToDict
from google.protobuf.message import Message as ProtoMessage
from jsonrpc.jsonrpc2 import JSONRPC20Response

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
    | ListTasksResponse
)
"""Type alias for possible event types produced by handlers."""


def agent_card_to_dict(
    card: AgentCard, preserving_proto_field_name: bool = False
) -> dict[str, Any]:
    """Convert AgentCard to dict and inject backward compatibility fields."""
    result = MessageToDict(
        card, preserving_proto_field_name=preserving_proto_field_name
    )

    def fname(name_camel: str, name_snake: str) -> str:
        """Select camel or snake case based on preserving_proto_field_name."""
        return name_snake if preserving_proto_field_name else name_camel

    # supportsAuthenticatedExtendedCard
    if card.capabilities.extended_agent_card:
        result[
            fname(
                'supportsAuthenticatedExtendedCard',
                'supports_authenticated_extended_card',
            )
        ] = True

    # top-level connection fields
    if card.supported_interfaces:
        preferred_iface = card.supported_interfaces[0]
        result['url'] = preferred_iface.url
        result[fname('preferredTransport', 'preferred_transport')] = (
            preferred_iface.protocol_binding
        )
        result[fname('protocolVersion', 'protocol_version')] = (
            preferred_iface.protocol_version
        )

        if len(card.supported_interfaces) > 1:
            additional_ifaces = [
                {
                    'url': iface.url,
                    'transport': iface.protocol_binding,
                }
                for iface in card.supported_interfaces[1:]
            ]
            result[fname('additionalInterfaces', 'additional_interfaces')] = (
                additional_ifaces
            )

    # security mappings
    def map_security_requirements(reqs: Any) -> list[dict[str, list[str]]]:
        """Convert a 1.0.0 Protobuf security requirement list into the legacy format."""
        security_list = []
        for req in reqs:
            req_dict = {}
            for scheme_name, string_list in req.schemes.items():
                req_dict[scheme_name] = list(string_list.list)
            security_list.append(req_dict)
        return security_list

    if card.security_requirements:
        result['security'] = map_security_requirements(
            card.security_requirements
        )

    skills_key = fname('skills', 'skills')
    result.setdefault(skills_key, [])
    if skills_key in result and isinstance(result[skills_key], list):
        for i, skill in enumerate(card.skills):
            if skill.security_requirements:
                result[skills_key][i]['security'] = map_security_requirements(
                    skill.security_requirements
                )

    result.setdefault(fname('defaultInputModes', 'default_input_modes'), [])
    result.setdefault(fname('defaultOutputModes', 'default_output_modes'), [])
    result.setdefault('capabilities', {})

    # securitySchemes mappings
    schemes_key = fname('securitySchemes', 'security_schemes')
    if schemes_key in result:
        scheme_type_map = {
            'apiKeySecurityScheme': 'apiKey',
            'api_key_security_scheme': 'apiKey',
            'httpAuthSecurityScheme': 'http',
            'http_auth_security_scheme': 'http',
            'oauth2SecurityScheme': 'oauth2',
            'oauth2_security_scheme': 'oauth2',
            'openIdConnectSecurityScheme': 'openIdConnect',
            'open_id_connect_security_scheme': 'openIdConnect',
            'mtlsSecurityScheme': 'mutualTLS',
            'mtls_security_scheme': 'mutualTLS',
        }
        for scheme_data in result[schemes_key].values():
            for proto_key, json_type in scheme_type_map.items():
                if proto_key in scheme_data:
                    details = scheme_data.pop(proto_key)
                    scheme_data['type'] = json_type

                    # Map modern 'location' to legacy 'in'
                    if json_type == 'apiKey' and 'location' in details:
                        details['in'] = details.pop('location')

                    scheme_data.update(details)
                    break

    return result


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

    if isinstance(response, A2AException | JSONRPCError):
        return build_error_response(request_id, response)

    # If response is not an expected success type and not an error,
    # it's an invalid type of response from the agent for this method.
    error = InvalidAgentResponseError(
        message='Agent returned invalid type response for this method'
    )
    return build_error_response(request_id, error)
