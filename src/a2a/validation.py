"""JSON Schema validation for A2A protocol messages.

This module provides JSON Schema generation and validation for all A2A
message types, ensuring protocol compliance for incoming and outgoing messages.
"""

from functools import lru_cache
from typing import Any, TypeVar

import jsonschema

from pydantic import BaseModel

from a2a.types import (
    AgentCard,
    CancelTaskRequest,
    CancelTaskResponse,
    GetTaskPushNotificationConfigParams,
    GetTaskPushNotificationConfigRequest,
    GetTaskPushNotificationConfigResponse,
    GetTaskRequest,
    GetTaskResponse,
    JSONRPCErrorResponse,
    JSONRPCRequest,
    Message,
    MessageSendParams,
    SendMessageRequest,
    SendMessageResponse,
    SendStreamingMessageRequest,
    SendStreamingMessageResponse,
    SetTaskPushNotificationConfigRequest,
    SetTaskPushNotificationConfigResponse,
    Task,
    TaskArtifactUpdateEvent,
    TaskIdParams,
    TaskPushNotificationConfig,
    TaskQueryParams,
    TaskResubscriptionRequest,
    TaskStatusUpdateEvent,
)


T = TypeVar('T', bound=BaseModel)

_MESSAGE_TYPES: tuple[type[BaseModel], ...] = (
    SendMessageRequest,
    SendStreamingMessageRequest,
    GetTaskRequest,
    CancelTaskRequest,
    SetTaskPushNotificationConfigRequest,
    GetTaskPushNotificationConfigRequest,
    TaskResubscriptionRequest,
    SendMessageResponse,
    SendStreamingMessageResponse,
    GetTaskResponse,
    CancelTaskResponse,
    SetTaskPushNotificationConfigResponse,
    GetTaskPushNotificationConfigResponse,
    JSONRPCRequest,
    JSONRPCErrorResponse,
    Message,
    Task,
    TaskStatusUpdateEvent,
    TaskArtifactUpdateEvent,
    AgentCard,
    TaskQueryParams,
    TaskIdParams,
    MessageSendParams,
    TaskPushNotificationConfig,
    GetTaskPushNotificationConfigParams,
)

_TYPE_SCHEMA_CACHE: dict[type[BaseModel], dict[str, Any]] = {}


class ValidationError(Exception):
    """Raised when message validation fails against JSON Schema."""

    def __init__(
        self,
        message: str,
        errors: list[dict[str, Any]] | None = None,
        schema: dict[str, Any] | None = None,
        instance: Any = None,
    ):
        super().__init__(message)
        self.errors = errors or []
        self.schema = schema
        self.instance = instance


def get_schema_for_type(model_type: type[BaseModel]) -> dict[str, Any]:
    """Generate JSON Schema for a Pydantic model type.

    Args:
        model_type: A Pydantic BaseModel subclass.

    Returns:
        A dictionary containing the JSON Schema for the model.
    """
    if model_type in _TYPE_SCHEMA_CACHE:
        return _TYPE_SCHEMA_CACHE[model_type]

    schema = model_type.model_json_schema(
        mode='serialization',
        by_alias=True,
        ref_template='#/definitions/{model}',
    )

    if '$defs' in schema:
        definitions = schema.pop('$defs')
        if definitions:
            schema['definitions'] = definitions

    _TYPE_SCHEMA_CACHE[model_type] = schema
    return schema


@lru_cache(maxsize=1)
def get_protocol_schemas() -> dict[str, dict[str, Any]]:
    """Generate JSON Schemas for all A2A protocol message types.

    Returns:
        A dictionary mapping type names to their JSON Schemas.
    """
    schemas: dict[str, dict[str, Any]] = {}

    for model_type in _MESSAGE_TYPES:
        schema = get_schema_for_type(model_type)
        schemas[model_type.__name__] = schema

    return schemas


def get_request_schemas() -> dict[str, dict[str, Any]]:
    """Get JSON Schemas for all A2A request types.

    Returns:
        Dictionary of request type names to their schemas.
    """
    request_types = (
        SendMessageRequest,
        SendStreamingMessageRequest,
        GetTaskRequest,
        CancelTaskRequest,
        SetTaskPushNotificationConfigRequest,
        GetTaskPushNotificationConfigRequest,
        TaskResubscriptionRequest,
    )
    return {t.__name__: get_schema_for_type(t) for t in request_types}


def get_response_schemas() -> dict[str, dict[str, Any]]:
    """Get JSON Schemas for all A2A response types.

    Returns:
        Dictionary of response type names to their schemas.
    """
    response_types = (
        SendMessageResponse,
        SendStreamingMessageResponse,
        GetTaskResponse,
        CancelTaskResponse,
        SetTaskPushNotificationConfigResponse,
        GetTaskPushNotificationConfigResponse,
    )
    return {t.__name__: get_schema_for_type(t) for t in response_types}


def get_event_schemas() -> dict[str, dict[str, Any]]:
    """Get JSON Schemas for all A2A event types.

    Returns:
        Dictionary of event type names to their schemas.
    """
    event_types = (
        TaskStatusUpdateEvent,
        TaskArtifactUpdateEvent,
    )
    return {t.__name__: get_schema_for_type(t) for t in event_types}


def validate_message(
    data: dict[str, Any],
    model_type: type[T],
    *,
    strict: bool = True,
) -> T:
    """Validate message data against a Pydantic model's JSON Schema.

    This performs both JSON Schema validation and Pydantic model validation.

    Args:
        data: The raw message data to validate.
        model_type: The expected Pydantic model type.
        strict: Whether to use strict validation mode.

    Returns:
        The validated and parsed model instance.

    Raises:
        ValidationError: If validation fails against the schema.
    """
    schema = get_schema_for_type(model_type)

    try:
        jsonschema.validate(
            instance=data,
            schema=schema,
            cls=jsonschema.Draft7Validator,
        )
    except jsonschema.ValidationError as e:
        raise ValidationError(
            f'JSON Schema validation failed: {e.message}',
            errors=[{'path': list(e.path), 'message': e.message}],
            schema=schema,
            instance=data,
        ) from e

    try:
        return model_type.model_validate(data, strict=strict)
    except Exception as e:
        raise ValidationError(
            f'Pydantic validation failed: {e}',
            schema=schema,
            instance=data,
        ) from e


def _validate_against_types(
    data: dict[str, Any],
    model_types: tuple[type[BaseModel], ...],
    category_name: str,
) -> BaseModel:
    """Validate data against multiple model types and return first match.

    Args:
        data: Raw data to validate.
        model_types: Tuple of model types to try.
        category_name: Name of the category for error messages (e.g., 'request', 'response').

    Returns:
        The validated model instance.

    Raises:
        ValidationError: If data doesn't match any of the provided types.
    """
    errors: list[dict[str, Any]] = []

    for model_type in model_types:
        try:
            return validate_message(data, model_type, strict=False)
        except ValidationError as e:
            errors.append(
                {
                    'type': model_type.__name__,
                    'path': e.errors[0].get('path', []) if e.errors else [],
                    'message': e.errors[0].get('message', str(e))
                    if e.errors
                    else str(e),
                }
            )

    error_details = '; '.join(
        f'{e["type"]}: {e["message"]} (path: {".".join(map(str, e["path"])) or "root"})'
        for e in errors
    )
    raise ValidationError(
        f'Data does not match any known A2A {category_name} type. '
        f'Attempted types: {[e["type"] for e in errors]}. Details: {error_details}',
        errors=errors,
        instance=data,
    )


def validate_request(data: dict[str, Any]) -> BaseModel:
    """Validate and parse an A2A request message.

    Attempts to validate against all known request types and returns
    the first successful match.

    Args:
        data: Raw request data to validate.

    Returns:
        The validated request model instance.

    Raises:
        ValidationError: If data doesn't match any request type.
    """
    request_types = (
        SendMessageRequest,
        SendStreamingMessageRequest,
        GetTaskRequest,
        CancelTaskRequest,
        SetTaskPushNotificationConfigRequest,
        GetTaskPushNotificationConfigRequest,
        TaskResubscriptionRequest,
    )

    return _validate_against_types(data, request_types, 'request')


def validate_response(data: dict[str, Any]) -> BaseModel:
    """Validate and parse an A2A response message.

    Args:
        data: Raw response data to validate.

    Returns:
        The validated response model instance.

    Raises:
        ValidationError: If validation fails.
    """
    response_types = (
        SendMessageResponse,
        SendStreamingMessageResponse,
        GetTaskResponse,
        CancelTaskResponse,
        SetTaskPushNotificationConfigResponse,
        GetTaskPushNotificationConfigResponse,
    )

    return _validate_against_types(data, response_types, 'response')


class MessageValidator:
    """A reusable validator for A2A messages with caching.

    This class provides efficient validation by caching schemas and
    supporting batch validation operations.
    """

    def __init__(self, *, strict: bool = True):
        """Initialize the message validator.

        Args:
            strict: Whether to use strict validation mode by default.
        """
        self._strict = strict
        self._schemas = get_protocol_schemas()

    def validate(
        self,
        data: dict[str, Any],
        model_type: type[T],
    ) -> T:
        """Validate data against a specific model type.

        Args:
            data: Raw data to validate.
            model_type: Expected model type.

        Returns:
            Validated model instance.

        Raises:
            ValidationError: If validation fails.
        """
        return validate_message(data, model_type, strict=self._strict)

    def validate_batch(
        self,
        messages: list[dict[str, Any]],
        model_type: type[T],
    ) -> list[T]:
        """Validate multiple messages of the same type.

        Args:
            messages: List of raw message data.
            model_type: Expected model type for all messages.

        Returns:
            List of validated model instances.

        Raises:
            ValidationError: If any message fails validation.
        """
        return [self.validate(msg, model_type) for msg in messages]

    def get_schema(self, type_name: str) -> dict[str, Any] | None:
        """Get a cached schema by type name.

        Args:
            type_name: Name of the type to get schema for.

        Returns:
            JSON Schema dictionary or None if not found.
        """
        return self._schemas.get(type_name)

    def list_schemas(self) -> list[str]:
        """List all available schema type names.

        Returns:
            List of type names with cached schemas.
        """
        return list(self._schemas.keys())

    def clear_cache(self) -> None:
        """Clear all cached schemas."""
        self._schemas.clear()
        _TYPE_SCHEMA_CACHE.clear()
        get_protocol_schemas.cache_clear()
        self._schemas = get_protocol_schemas()
