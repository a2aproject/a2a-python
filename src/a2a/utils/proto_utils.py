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

"""Utilities for converting between proto types and internal types.

Since we now use proto types directly as our internal types, most of these
conversions are identity operations. This module maintains API compatibility
with code that expects conversion utilities.
"""

from typing import Any, Union

from a2a.types.a2a_pb2 import (
    CancelTaskRequest,
    GetTaskRequest,
    Message,
    SendMessageRequest,
    SetTaskPushNotificationConfigRequest,
    StreamResponse,
    SubscribeToTaskRequest,
    Task,
    TaskArtifactUpdateEvent,
    TaskPushNotificationConfig,
    TaskStatusUpdateEvent,
)

# Define Event type locally to avoid circular imports
Event = Message | Task | TaskStatusUpdateEvent | TaskArtifactUpdateEvent


class FromProto:
    """Converts from proto types to internal types.
    
    Since we now use proto types directly, these are mostly identity operations.
    """

    @staticmethod
    def message_send_params(proto: SendMessageRequest) -> SendMessageRequest:
        """Convert SendMessageRequest proto to internal type.
        
        Since we use proto types directly, this is an identity operation.
        """
        return proto

    @staticmethod
    def task_id_params(proto: CancelTaskRequest | SubscribeToTaskRequest | GetTaskRequest) -> CancelTaskRequest | SubscribeToTaskRequest | GetTaskRequest:
        """Convert task ID params proto to internal type.
        
        Since we use proto types directly, this is an identity operation.
        """
        return proto

    @staticmethod
    def task_push_notification_config_request(
        proto: SetTaskPushNotificationConfigRequest,
    ) -> TaskPushNotificationConfig:
        """Convert SetTaskPushNotificationConfigRequest proto to TaskPushNotificationConfig.
        
        Extracts the config from the request.
        """
        return proto.config if proto.config else TaskPushNotificationConfig()


class ToProto:
    """Converts from internal types to proto types.
    
    Since we now use proto types directly, these are mostly identity operations.
    """

    @staticmethod
    def task(task: Task) -> Task:
        """Convert internal Task to proto Task.
        
        Since we use proto types directly, this is an identity operation.
        """
        return task

    @staticmethod
    def message(message: Message) -> Message:
        """Convert internal Message to proto Message.
        
        Since we use proto types directly, this is an identity operation.
        """
        return message

    @staticmethod
    def task_or_message(task_or_message: Task | Message) -> Task | Message:
        """Convert internal Task or Message to proto.
        
        Since we use proto types directly, this is an identity operation.
        """
        return task_or_message

    @staticmethod
    def task_push_notification_config(
        config: TaskPushNotificationConfig,
    ) -> TaskPushNotificationConfig:
        """Convert internal TaskPushNotificationConfig to proto.
        
        Since we use proto types directly, this is an identity operation.
        """
        return config

    @staticmethod
    def stream_response(event: Event) -> StreamResponse:
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
