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

"""Utilities for working with proto types.

This module provides helper functions for common proto type operations.
"""

from typing import TYPE_CHECKING, Any, TypedDict

from google.api.field_behavior_pb2 import FieldBehavior, field_behavior
from google.protobuf.descriptor import FieldDescriptor
from google.protobuf.json_format import ParseDict
from google.protobuf.message import Message as ProtobufMessage
from google.rpc import error_details_pb2

from a2a.utils.errors import InvalidParamsError


if TYPE_CHECKING:
    from starlette.datastructures import QueryParams
else:
    try:
        from starlette.datastructures import QueryParams
    except ImportError:
        QueryParams = Any

from a2a.types.a2a_pb2 import (
    Message,
    StreamResponse,
    Task,
    TaskArtifactUpdateEvent,
    TaskStatusUpdateEvent,
)


# Define Event type locally to avoid circular imports
Event = Message | Task | TaskStatusUpdateEvent | TaskArtifactUpdateEvent


def to_stream_response(event: Event) -> StreamResponse:
    """Convert internal Event to StreamResponse proto.

    Args:
        event: The event (Task, Message, TaskStatusUpdateEvent, TaskArtifactUpdateEvent)

    Returns:
        A StreamResponse proto with the appropriate field set.
    """
    response = StreamResponse()
    if isinstance(event, Task):
        response.task.CopyFrom(event)
    elif isinstance(event, Message):
        response.message.CopyFrom(event)
    elif isinstance(event, TaskStatusUpdateEvent):
        response.status_update.CopyFrom(event)
    elif isinstance(event, TaskArtifactUpdateEvent):
        response.artifact_update.CopyFrom(event)
    return response


def make_dict_serializable(value: Any) -> Any:
    """Dict pre-processing utility: converts non-serializable values to serializable form.

    Use this when you want to normalize a dictionary before dict->Struct conversion.

    Args:
        value: The value to convert.

    Returns:
        A serializable value.
    """
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    if isinstance(value, dict):
        return {k: make_dict_serializable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [make_dict_serializable(item) for item in value]
    return str(value)


def normalize_large_integers_to_strings(
    value: Any, max_safe_digits: int = 15
) -> Any:
    """Integer preprocessing utility: converts large integers to strings.

    Use this when you want to convert large integers to strings considering
    JavaScript's MAX_SAFE_INTEGER (2^53 - 1) limitation.

    Args:
        value: The value to convert.
        max_safe_digits: Maximum safe integer digits (default: 15).

    Returns:
        A normalized value.
    """
    max_safe_int = 10**max_safe_digits - 1

    def _normalize(item: Any) -> Any:
        if isinstance(item, int) and abs(item) > max_safe_int:
            return str(item)
        if isinstance(item, dict):
            return {k: _normalize(v) for k, v in item.items()}
        if isinstance(item, list | tuple):
            return [_normalize(i) for i in item]
        return item

    return _normalize(value)


def parse_string_integers_in_dict(value: Any, max_safe_digits: int = 15) -> Any:
    """String post-processing utility: converts large integer strings back to integers.

    Use this when you want to restore large integer strings to integers
    after Struct->dict conversion.

    Args:
        value: The value to convert.
        max_safe_digits: Maximum safe integer digits (default: 15).

    Returns:
        A parsed value.
    """
    if isinstance(value, dict):
        return {
            k: parse_string_integers_in_dict(v, max_safe_digits)
            for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        return [
            parse_string_integers_in_dict(item, max_safe_digits)
            for item in value
        ]
    if isinstance(value, str):
        # Handle potential negative numbers.
        stripped_value = value.lstrip('-')
        if stripped_value.isdigit() and len(stripped_value) > max_safe_digits:
            return int(value)
    return value


def parse_params(params: QueryParams, message: ProtobufMessage) -> None:
    """Converts REST query parameters back into a Protobuf message.

    Handles A2A-specific pre-processing before calling ParseDict:
    - Booleans: 'true'/'false' -> True/False
    - Repeated: Supports BOTH repeated keys and comma-separated values.
    - Others: Handles string->enum/timestamp/number conversion via ParseDict.

    See Also:
        https://a2a-protocol.org/latest/specification/#115-query-parameter-naming-for-request-parameters
    """
    descriptor = message.DESCRIPTOR
    fields = {f.camelcase_name: f for f in descriptor.fields}
    processed: dict[str, Any] = {}

    keys = params.keys()

    for k in keys:
        if k not in fields:
            continue

        field = fields[k]
        v_list = params.getlist(k)

        if field.label == field.LABEL_REPEATED:
            accumulated: list[Any] = []
            for v in v_list:
                if not v:
                    continue
                if isinstance(v, str):
                    accumulated.extend([x for x in v.split(',') if x])
                else:
                    accumulated.append(v)
            processed[k] = accumulated
        else:
            # For non-repeated fields, the last one wins.
            raw_val = v_list[-1]
            if raw_val is not None:
                parsed_val: Any = raw_val
                if field.type == field.TYPE_BOOL and isinstance(raw_val, str):
                    parsed_val = raw_val.lower() == 'true'
                processed[k] = parsed_val

    ParseDict(processed, message, ignore_unknown_fields=True)


class ValidationDetail(TypedDict):
    """Structured validation error detail."""

    field: str
    message: str


def _check_required_field_violation(
    msg: ProtobufMessage, field: FieldDescriptor
) -> ValidationDetail | None:
    """Check if a required field is missing or invalid."""
    val = getattr(msg, field.name)
    if field.is_repeated:
        if not val:
            return ValidationDetail(
                field=field.name,
                message='Field must contain at least one element.',
            )
    elif field.has_presence:
        if not msg.HasField(field.name):
            return ValidationDetail(
                field=field.name, message='Field is required.'
            )
    elif val == field.default_value:
        return ValidationDetail(field=field.name, message='Field is required.')
    return None


def _append_nested_errors(
    errors: list[ValidationDetail],
    prefix: str,
    sub_errs: list[ValidationDetail],
) -> None:
    """Format nested validation errors and append to errors list."""
    for sub in sub_errs:
        sub_field = sub['field']
        errors.append(
            ValidationDetail(
                field=f'{prefix}.{sub_field}' if sub_field else prefix,
                message=sub['message'],
            )
        )


def _recurse_validation(
    msg: ProtobufMessage, field: FieldDescriptor
) -> list[ValidationDetail]:
    """Recurse validation for nested messages and map fields."""
    errors: list[ValidationDetail] = []
    if field.type != FieldDescriptor.TYPE_MESSAGE:
        return errors

    val = getattr(msg, field.name)
    if not field.is_repeated:
        if msg.HasField(field.name):
            sub_errs = _validate_proto_required_fields_internal(val)
            _append_nested_errors(errors, field.name, sub_errs)
    elif field.message_type.GetOptions().map_entry:
        for k, v in val.items():
            if isinstance(v, ProtobufMessage):
                sub_errs = _validate_proto_required_fields_internal(v)
                _append_nested_errors(errors, f'{field.name}[{k}]', sub_errs)
    else:
        for i, item in enumerate(val):
            sub_errs = _validate_proto_required_fields_internal(item)
            _append_nested_errors(errors, f'{field.name}[{i}]', sub_errs)
    return errors


def _validate_proto_required_fields_internal(
    msg: ProtobufMessage,
) -> list[ValidationDetail]:
    """Internal validation that returns a list of error dictionaries."""
    desc = msg.DESCRIPTOR
    errors: list[ValidationDetail] = []

    for field in desc.fields:
        options = field.GetOptions()
        if FieldBehavior.REQUIRED in options.Extensions[field_behavior]:
            violation = _check_required_field_violation(msg, field)
            if violation:
                errors.append(violation)
        errors.extend(_recurse_validation(msg, field))
    return errors


def validate_proto_required_fields(msg: ProtobufMessage) -> None:
    """Validate that all fields marked as REQUIRED are present on the proto message.

    Args:
        msg: The Protobuf message to validate.

    Raises:
        InvalidParamsError: If a required field is missing or empty.
    """
    errors = _validate_proto_required_fields_internal(msg)

    if errors:
        raise InvalidParamsError(
            message='Validation failed', data={'errors': errors}
        )


def validation_errors_to_bad_request(
    errors: list[ValidationDetail],
) -> error_details_pb2.BadRequest:
    """Convert validation error details to a gRPC BadRequest proto."""
    bad_request = error_details_pb2.BadRequest()
    for err in errors:
        violation = bad_request.field_violations.add()
        violation.field = err['field']
        violation.description = err['message']
    return bad_request


def bad_request_to_validation_errors(
    bad_request: error_details_pb2.BadRequest,
) -> list[ValidationDetail]:
    """Convert a gRPC BadRequest proto to validation error details."""
    return [
        ValidationDetail(field=v.field, message=v.description)
        for v in bad_request.field_violations
    ]
