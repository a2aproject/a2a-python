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

"""A2A types module.

This module provides the protobuf-generated types for the A2A protocol.
The Google API proto dependencies must be imported before the a2a_pb2 module.
"""

# Pre-load Google API proto dependencies required by a2a_pb2.py
# These must be imported before a2a_pb2 to ensure the descriptor pool
# has the required proto definitions.
from google.api import annotations_pb2 as _annotations_pb2
from google.api import client_pb2 as _client_pb2
from google.api import field_behavior_pb2 as _field_behavior_pb2
from google.protobuf import empty_pb2 as _empty_pb2
from google.protobuf import struct_pb2 as _struct_pb2
from google.protobuf import timestamp_pb2 as _timestamp_pb2

# Now import and re-export all types from a2a_pb2
from a2a.types.a2a_pb2 import (
    APIKeySecurityScheme,
    AgentCapabilities,
    AgentCard,
    AgentCardSignature,
    AgentExtension,
    AgentInterface,
    AgentProvider,
    AgentSkill,
    Artifact,
    AuthenticationInfo,
    AuthorizationCodeOAuthFlow,
    CancelTaskRequest,
    ClientCredentialsOAuthFlow,
    DataPart,
    DeleteTaskPushNotificationConfigRequest,
    FilePart,
    GetExtendedAgentCardRequest,
    GetTaskPushNotificationConfigRequest,
    GetTaskRequest,
    HTTPAuthSecurityScheme,
    ImplicitOAuthFlow,
    ListTaskPushNotificationConfigRequest,
    ListTaskPushNotificationConfigResponse,
    ListTasksRequest,
    ListTasksResponse,
    Message,
    MutualTlsSecurityScheme,
    OAuth2SecurityScheme,
    OAuthFlows,
    OpenIdConnectSecurityScheme,
    Part,
    PasswordOAuthFlow,
    PushNotificationConfig,
    Role,
    Security,
    SecurityScheme,
    SendMessageConfiguration,
    SendMessageRequest,
    SendMessageResponse,
    SetTaskPushNotificationConfigRequest,
    StreamResponse,
    StringList,
    SubscribeToTaskRequest,
    Task,
    TaskArtifactUpdateEvent,
    TaskPushNotificationConfig,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)

# Import SDK-specific types from extras
from a2a.types.extras import (
    A2AError,
    # Request union type
    A2ARequest,
    AuthenticatedExtendedCardNotConfiguredError,
    CancelTaskResponse,
    CancelTaskSuccessResponse,
    ContentTypeNotSupportedError,
    # Note: ListTaskPushNotificationConfigResponse is already imported from a2a_pb2
    DeleteTaskPushNotificationConfigResponse,
    DeleteTaskPushNotificationConfigSuccessResponse,
    GetAuthenticatedExtendedCardResponse,
    GetAuthenticatedExtendedCardSuccessResponse,
    GetTaskPushNotificationConfigResponse,
    GetTaskPushNotificationConfigSuccessResponse,
    # Response wrapper types (RootModels)
    GetTaskResponse,
    # Success response types
    GetTaskSuccessResponse,
    InternalError,
    InvalidAgentResponseError,
    InvalidParamsError,
    InvalidRequestError,
    JSONParseError,
    # Error types
    JSONRPCError,
    JSONRPCErrorResponse,
    # JSON-RPC types
    JSONRPCRequest,
    JSONRPCResponse,
    ListTaskPushNotificationConfigSuccessResponse,
    MethodNotFoundError,
    PushNotificationNotSupportedError,
    SendMessageSuccessResponse,
    # Note: SendMessageResponse is already imported from a2a_pb2
    SendStreamingMessageResponse,
    SendStreamingMessageSuccessResponse,
    SetTaskPushNotificationConfigResponse,
    SetTaskPushNotificationConfigSuccessResponse,
    TaskNotCancelableError,
    TaskNotFoundError,
    # Aliases for backward compatibility
    TaskResubscriptionRequest,
    TransportProtocol,
    UnsupportedOperationError,
)


__all__ = [
    'A2AError',
    'A2ARequest',
    'APIKeySecurityScheme',
    # Proto types
    'AgentCapabilities',
    'AgentCard',
    'AgentCardSignature',
    'AgentExtension',
    'AgentInterface',
    'AgentProvider',
    'AgentSkill',
    'Artifact',
    'AuthenticatedExtendedCardNotConfiguredError',
    'AuthenticationInfo',
    'AuthorizationCodeOAuthFlow',
    'CancelTaskRequest',
    'CancelTaskResponse',
    'CancelTaskSuccessResponse',
    'ClientCredentialsOAuthFlow',
    'ContentTypeNotSupportedError',
    'DataPart',
    'DeleteTaskPushNotificationConfigRequest',
    'DeleteTaskPushNotificationConfigResponse',
    'DeleteTaskPushNotificationConfigSuccessResponse',
    'FilePart',
    'GetAuthenticatedExtendedCardResponse',
    'GetAuthenticatedExtendedCardSuccessResponse',
    'GetExtendedAgentCardRequest',
    'GetTaskPushNotificationConfigRequest',
    'GetTaskPushNotificationConfigResponse',
    'GetTaskPushNotificationConfigSuccessResponse',
    'GetTaskRequest',
    'GetTaskResponse',
    'GetTaskSuccessResponse',
    'HTTPAuthSecurityScheme',
    'ImplicitOAuthFlow',
    'InternalError',
    'InvalidAgentResponseError',
    'InvalidParamsError',
    'InvalidRequestError',
    'JSONParseError',
    'JSONRPCError',
    'JSONRPCErrorResponse',
    'JSONRPCRequest',
    'JSONRPCResponse',
    'ListTaskPushNotificationConfigRequest',
    'ListTaskPushNotificationConfigResponse',
    'ListTaskPushNotificationConfigSuccessResponse',
    'ListTasksRequest',
    'ListTasksResponse',
    'Message',
    'MethodNotFoundError',
    'MutualTlsSecurityScheme',
    'OAuth2SecurityScheme',
    'OAuthFlows',
    'OpenIdConnectSecurityScheme',
    'Part',
    'PasswordOAuthFlow',
    'PushNotificationConfig',
    'PushNotificationNotSupportedError',
    'Role',
    'Security',
    'SecurityScheme',
    'SendMessageConfiguration',
    'SendMessageRequest',
    'SendMessageResponse',
    'SendMessageSuccessResponse',
    'SendStreamingMessageResponse',
    'SendStreamingMessageSuccessResponse',
    'SetTaskPushNotificationConfigRequest',
    'SetTaskPushNotificationConfigResponse',
    'SetTaskPushNotificationConfigSuccessResponse',
    'StreamResponse',
    'StringList',
    'SubscribeToTaskRequest',
    'Task',
    'TaskArtifactUpdateEvent',
    'TaskNotCancelableError',
    'TaskNotFoundError',
    'TaskPushNotificationConfig',
    # SDK-specific types from extras
    'TaskResubscriptionRequest',
    'TaskState',
    'TaskStatus',
    'TaskStatusUpdateEvent',
    'TransportProtocol',
    'UnsupportedOperationError',
]
