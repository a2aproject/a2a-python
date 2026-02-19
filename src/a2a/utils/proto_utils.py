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

from typing import Any

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
