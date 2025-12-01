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
        response.msg.CopyFrom(event)
    elif isinstance(event, TaskStatusUpdateEvent):
        response.status_update.CopyFrom(event)
    elif isinstance(event, TaskArtifactUpdateEvent):
        response.artifact_update.CopyFrom(event)
    return response
