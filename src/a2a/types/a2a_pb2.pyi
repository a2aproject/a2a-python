import datetime

from collections.abc import Iterable as _Iterable
from collections.abc import Mapping as _Mapping
from typing import ClassVar as _ClassVar

from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from google.protobuf import struct_pb2 as _struct_pb2
from google.protobuf import timestamp_pb2 as _timestamp_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper

DESCRIPTOR: _descriptor.FileDescriptor

class TaskState(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    TASK_STATE_UNSPECIFIED: _ClassVar[TaskState]
    TASK_STATE_SUBMITTED: _ClassVar[TaskState]
    TASK_STATE_WORKING: _ClassVar[TaskState]
    TASK_STATE_COMPLETED: _ClassVar[TaskState]
    TASK_STATE_FAILED: _ClassVar[TaskState]
    TASK_STATE_CANCELLED: _ClassVar[TaskState]
    TASK_STATE_INPUT_REQUIRED: _ClassVar[TaskState]
    TASK_STATE_REJECTED: _ClassVar[TaskState]
    TASK_STATE_AUTH_REQUIRED: _ClassVar[TaskState]

class Role(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    ROLE_UNSPECIFIED: _ClassVar[Role]
    ROLE_USER: _ClassVar[Role]
    ROLE_AGENT: _ClassVar[Role]
TASK_STATE_UNSPECIFIED: TaskState
TASK_STATE_SUBMITTED: TaskState
TASK_STATE_WORKING: TaskState
TASK_STATE_COMPLETED: TaskState
TASK_STATE_FAILED: TaskState
TASK_STATE_CANCELLED: TaskState
TASK_STATE_INPUT_REQUIRED: TaskState
TASK_STATE_REJECTED: TaskState
TASK_STATE_AUTH_REQUIRED: TaskState
ROLE_UNSPECIFIED: Role
ROLE_USER: Role
ROLE_AGENT: Role

class SendMessageConfiguration(_message.Message):
    __slots__ = ()
    ACCEPTED_OUTPUT_MODES_FIELD_NUMBER: _ClassVar[int]
    PUSH_NOTIFICATION_CONFIG_FIELD_NUMBER: _ClassVar[int]
    HISTORY_LENGTH_FIELD_NUMBER: _ClassVar[int]
    BLOCKING_FIELD_NUMBER: _ClassVar[int]
    accepted_output_modes: _containers.RepeatedScalarFieldContainer[str]
    push_notification_config: PushNotificationConfig
    history_length: int
    blocking: bool
    def __init__(self, accepted_output_modes: _Iterable[str] | None = ..., push_notification_config: PushNotificationConfig | _Mapping | None = ..., history_length: int | None = ..., blocking: bool | None = ...) -> None: ...

class Task(_message.Message):
    __slots__ = ()
    ID_FIELD_NUMBER: _ClassVar[int]
    CONTEXT_ID_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    ARTIFACTS_FIELD_NUMBER: _ClassVar[int]
    HISTORY_FIELD_NUMBER: _ClassVar[int]
    METADATA_FIELD_NUMBER: _ClassVar[int]
    id: str
    context_id: str
    status: TaskStatus
    artifacts: _containers.RepeatedCompositeFieldContainer[Artifact]
    history: _containers.RepeatedCompositeFieldContainer[Message]
    metadata: _struct_pb2.Struct
    def __init__(self, id: str | None = ..., context_id: str | None = ..., status: TaskStatus | _Mapping | None = ..., artifacts: _Iterable[Artifact | _Mapping] | None = ..., history: _Iterable[Message | _Mapping] | None = ..., metadata: _struct_pb2.Struct | _Mapping | None = ...) -> None: ...

class TaskStatus(_message.Message):
    __slots__ = ()
    STATE_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    TIMESTAMP_FIELD_NUMBER: _ClassVar[int]
    state: TaskState
    message: Message
    timestamp: _timestamp_pb2.Timestamp
    def __init__(self, state: TaskState | str | None = ..., message: Message | _Mapping | None = ..., timestamp: datetime.datetime | _timestamp_pb2.Timestamp | _Mapping | None = ...) -> None: ...

class Part(_message.Message):
    __slots__ = ()
    TEXT_FIELD_NUMBER: _ClassVar[int]
    FILE_FIELD_NUMBER: _ClassVar[int]
    DATA_FIELD_NUMBER: _ClassVar[int]
    METADATA_FIELD_NUMBER: _ClassVar[int]
    text: str
    file: FilePart
    data: DataPart
    metadata: _struct_pb2.Struct
    def __init__(self, text: str | None = ..., file: FilePart | _Mapping | None = ..., data: DataPart | _Mapping | None = ..., metadata: _struct_pb2.Struct | _Mapping | None = ...) -> None: ...

class FilePart(_message.Message):
    __slots__ = ()
    FILE_WITH_URI_FIELD_NUMBER: _ClassVar[int]
    FILE_WITH_BYTES_FIELD_NUMBER: _ClassVar[int]
    MEDIA_TYPE_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    file_with_uri: str
    file_with_bytes: bytes
    media_type: str
    name: str
    def __init__(self, file_with_uri: str | None = ..., file_with_bytes: bytes | None = ..., media_type: str | None = ..., name: str | None = ...) -> None: ...

class DataPart(_message.Message):
    __slots__ = ()
    DATA_FIELD_NUMBER: _ClassVar[int]
    data: _struct_pb2.Struct
    def __init__(self, data: _struct_pb2.Struct | _Mapping | None = ...) -> None: ...

class Message(_message.Message):
    __slots__ = ()
    MESSAGE_ID_FIELD_NUMBER: _ClassVar[int]
    CONTEXT_ID_FIELD_NUMBER: _ClassVar[int]
    TASK_ID_FIELD_NUMBER: _ClassVar[int]
    ROLE_FIELD_NUMBER: _ClassVar[int]
    PARTS_FIELD_NUMBER: _ClassVar[int]
    METADATA_FIELD_NUMBER: _ClassVar[int]
    EXTENSIONS_FIELD_NUMBER: _ClassVar[int]
    REFERENCE_TASK_IDS_FIELD_NUMBER: _ClassVar[int]
    message_id: str
    context_id: str
    task_id: str
    role: Role
    parts: _containers.RepeatedCompositeFieldContainer[Part]
    metadata: _struct_pb2.Struct
    extensions: _containers.RepeatedScalarFieldContainer[str]
    reference_task_ids: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, message_id: str | None = ..., context_id: str | None = ..., task_id: str | None = ..., role: Role | str | None = ..., parts: _Iterable[Part | _Mapping] | None = ..., metadata: _struct_pb2.Struct | _Mapping | None = ..., extensions: _Iterable[str] | None = ..., reference_task_ids: _Iterable[str] | None = ...) -> None: ...

class Artifact(_message.Message):
    __slots__ = ()
    ARTIFACT_ID_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    PARTS_FIELD_NUMBER: _ClassVar[int]
    METADATA_FIELD_NUMBER: _ClassVar[int]
    EXTENSIONS_FIELD_NUMBER: _ClassVar[int]
    artifact_id: str
    name: str
    description: str
    parts: _containers.RepeatedCompositeFieldContainer[Part]
    metadata: _struct_pb2.Struct
    extensions: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, artifact_id: str | None = ..., name: str | None = ..., description: str | None = ..., parts: _Iterable[Part | _Mapping] | None = ..., metadata: _struct_pb2.Struct | _Mapping | None = ..., extensions: _Iterable[str] | None = ...) -> None: ...

class TaskStatusUpdateEvent(_message.Message):
    __slots__ = ()
    TASK_ID_FIELD_NUMBER: _ClassVar[int]
    CONTEXT_ID_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    FINAL_FIELD_NUMBER: _ClassVar[int]
    METADATA_FIELD_NUMBER: _ClassVar[int]
    task_id: str
    context_id: str
    status: TaskStatus
    final: bool
    metadata: _struct_pb2.Struct
    def __init__(self, task_id: str | None = ..., context_id: str | None = ..., status: TaskStatus | _Mapping | None = ..., final: bool | None = ..., metadata: _struct_pb2.Struct | _Mapping | None = ...) -> None: ...

class TaskArtifactUpdateEvent(_message.Message):
    __slots__ = ()
    TASK_ID_FIELD_NUMBER: _ClassVar[int]
    CONTEXT_ID_FIELD_NUMBER: _ClassVar[int]
    ARTIFACT_FIELD_NUMBER: _ClassVar[int]
    APPEND_FIELD_NUMBER: _ClassVar[int]
    LAST_CHUNK_FIELD_NUMBER: _ClassVar[int]
    METADATA_FIELD_NUMBER: _ClassVar[int]
    task_id: str
    context_id: str
    artifact: Artifact
    append: bool
    last_chunk: bool
    metadata: _struct_pb2.Struct
    def __init__(self, task_id: str | None = ..., context_id: str | None = ..., artifact: Artifact | _Mapping | None = ..., append: bool | None = ..., last_chunk: bool | None = ..., metadata: _struct_pb2.Struct | _Mapping | None = ...) -> None: ...

class PushNotificationConfig(_message.Message):
    __slots__ = ()
    ID_FIELD_NUMBER: _ClassVar[int]
    URL_FIELD_NUMBER: _ClassVar[int]
    TOKEN_FIELD_NUMBER: _ClassVar[int]
    AUTHENTICATION_FIELD_NUMBER: _ClassVar[int]
    id: str
    url: str
    token: str
    authentication: AuthenticationInfo
    def __init__(self, id: str | None = ..., url: str | None = ..., token: str | None = ..., authentication: AuthenticationInfo | _Mapping | None = ...) -> None: ...

class AuthenticationInfo(_message.Message):
    __slots__ = ()
    SCHEMES_FIELD_NUMBER: _ClassVar[int]
    CREDENTIALS_FIELD_NUMBER: _ClassVar[int]
    schemes: _containers.RepeatedScalarFieldContainer[str]
    credentials: str
    def __init__(self, schemes: _Iterable[str] | None = ..., credentials: str | None = ...) -> None: ...

class AgentInterface(_message.Message):
    __slots__ = ()
    URL_FIELD_NUMBER: _ClassVar[int]
    PROTOCOL_BINDING_FIELD_NUMBER: _ClassVar[int]
    url: str
    protocol_binding: str
    def __init__(self, url: str | None = ..., protocol_binding: str | None = ...) -> None: ...

class AgentCard(_message.Message):
    __slots__ = ()
    class SecuritySchemesEntry(_message.Message):
        __slots__ = ()
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: SecurityScheme
        def __init__(self, key: str | None = ..., value: SecurityScheme | _Mapping | None = ...) -> None: ...
    PROTOCOL_VERSION_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    SUPPORTED_INTERFACES_FIELD_NUMBER: _ClassVar[int]
    URL_FIELD_NUMBER: _ClassVar[int]
    PREFERRED_TRANSPORT_FIELD_NUMBER: _ClassVar[int]
    ADDITIONAL_INTERFACES_FIELD_NUMBER: _ClassVar[int]
    PROVIDER_FIELD_NUMBER: _ClassVar[int]
    VERSION_FIELD_NUMBER: _ClassVar[int]
    DOCUMENTATION_URL_FIELD_NUMBER: _ClassVar[int]
    CAPABILITIES_FIELD_NUMBER: _ClassVar[int]
    SECURITY_SCHEMES_FIELD_NUMBER: _ClassVar[int]
    SECURITY_FIELD_NUMBER: _ClassVar[int]
    DEFAULT_INPUT_MODES_FIELD_NUMBER: _ClassVar[int]
    DEFAULT_OUTPUT_MODES_FIELD_NUMBER: _ClassVar[int]
    SKILLS_FIELD_NUMBER: _ClassVar[int]
    SUPPORTS_AUTHENTICATED_EXTENDED_CARD_FIELD_NUMBER: _ClassVar[int]
    SIGNATURES_FIELD_NUMBER: _ClassVar[int]
    ICON_URL_FIELD_NUMBER: _ClassVar[int]
    protocol_version: str
    name: str
    description: str
    supported_interfaces: _containers.RepeatedCompositeFieldContainer[AgentInterface]
    url: str
    preferred_transport: str
    additional_interfaces: _containers.RepeatedCompositeFieldContainer[AgentInterface]
    provider: AgentProvider
    version: str
    documentation_url: str
    capabilities: AgentCapabilities
    security_schemes: _containers.MessageMap[str, SecurityScheme]
    security: _containers.RepeatedCompositeFieldContainer[Security]
    default_input_modes: _containers.RepeatedScalarFieldContainer[str]
    default_output_modes: _containers.RepeatedScalarFieldContainer[str]
    skills: _containers.RepeatedCompositeFieldContainer[AgentSkill]
    supports_authenticated_extended_card: bool
    signatures: _containers.RepeatedCompositeFieldContainer[AgentCardSignature]
    icon_url: str
    def __init__(self, protocol_version: str | None = ..., name: str | None = ..., description: str | None = ..., supported_interfaces: _Iterable[AgentInterface | _Mapping] | None = ..., url: str | None = ..., preferred_transport: str | None = ..., additional_interfaces: _Iterable[AgentInterface | _Mapping] | None = ..., provider: AgentProvider | _Mapping | None = ..., version: str | None = ..., documentation_url: str | None = ..., capabilities: AgentCapabilities | _Mapping | None = ..., security_schemes: _Mapping[str, SecurityScheme] | None = ..., security: _Iterable[Security | _Mapping] | None = ..., default_input_modes: _Iterable[str] | None = ..., default_output_modes: _Iterable[str] | None = ..., skills: _Iterable[AgentSkill | _Mapping] | None = ..., supports_authenticated_extended_card: bool | None = ..., signatures: _Iterable[AgentCardSignature | _Mapping] | None = ..., icon_url: str | None = ...) -> None: ...

class AgentProvider(_message.Message):
    __slots__ = ()
    URL_FIELD_NUMBER: _ClassVar[int]
    ORGANIZATION_FIELD_NUMBER: _ClassVar[int]
    url: str
    organization: str
    def __init__(self, url: str | None = ..., organization: str | None = ...) -> None: ...

class AgentCapabilities(_message.Message):
    __slots__ = ()
    STREAMING_FIELD_NUMBER: _ClassVar[int]
    PUSH_NOTIFICATIONS_FIELD_NUMBER: _ClassVar[int]
    EXTENSIONS_FIELD_NUMBER: _ClassVar[int]
    STATE_TRANSITION_HISTORY_FIELD_NUMBER: _ClassVar[int]
    streaming: bool
    push_notifications: bool
    extensions: _containers.RepeatedCompositeFieldContainer[AgentExtension]
    state_transition_history: bool
    def __init__(self, streaming: bool | None = ..., push_notifications: bool | None = ..., extensions: _Iterable[AgentExtension | _Mapping] | None = ..., state_transition_history: bool | None = ...) -> None: ...

class AgentExtension(_message.Message):
    __slots__ = ()
    URI_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    REQUIRED_FIELD_NUMBER: _ClassVar[int]
    PARAMS_FIELD_NUMBER: _ClassVar[int]
    uri: str
    description: str
    required: bool
    params: _struct_pb2.Struct
    def __init__(self, uri: str | None = ..., description: str | None = ..., required: bool | None = ..., params: _struct_pb2.Struct | _Mapping | None = ...) -> None: ...

class AgentSkill(_message.Message):
    __slots__ = ()
    ID_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    TAGS_FIELD_NUMBER: _ClassVar[int]
    EXAMPLES_FIELD_NUMBER: _ClassVar[int]
    INPUT_MODES_FIELD_NUMBER: _ClassVar[int]
    OUTPUT_MODES_FIELD_NUMBER: _ClassVar[int]
    SECURITY_FIELD_NUMBER: _ClassVar[int]
    id: str
    name: str
    description: str
    tags: _containers.RepeatedScalarFieldContainer[str]
    examples: _containers.RepeatedScalarFieldContainer[str]
    input_modes: _containers.RepeatedScalarFieldContainer[str]
    output_modes: _containers.RepeatedScalarFieldContainer[str]
    security: _containers.RepeatedCompositeFieldContainer[Security]
    def __init__(self, id: str | None = ..., name: str | None = ..., description: str | None = ..., tags: _Iterable[str] | None = ..., examples: _Iterable[str] | None = ..., input_modes: _Iterable[str] | None = ..., output_modes: _Iterable[str] | None = ..., security: _Iterable[Security | _Mapping] | None = ...) -> None: ...

class AgentCardSignature(_message.Message):
    __slots__ = ()
    PROTECTED_FIELD_NUMBER: _ClassVar[int]
    SIGNATURE_FIELD_NUMBER: _ClassVar[int]
    HEADER_FIELD_NUMBER: _ClassVar[int]
    protected: str
    signature: str
    header: _struct_pb2.Struct
    def __init__(self, protected: str | None = ..., signature: str | None = ..., header: _struct_pb2.Struct | _Mapping | None = ...) -> None: ...

class TaskPushNotificationConfig(_message.Message):
    __slots__ = ()
    NAME_FIELD_NUMBER: _ClassVar[int]
    PUSH_NOTIFICATION_CONFIG_FIELD_NUMBER: _ClassVar[int]
    name: str
    push_notification_config: PushNotificationConfig
    def __init__(self, name: str | None = ..., push_notification_config: PushNotificationConfig | _Mapping | None = ...) -> None: ...

class StringList(_message.Message):
    __slots__ = ()
    LIST_FIELD_NUMBER: _ClassVar[int]
    list: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, list: _Iterable[str] | None = ...) -> None: ...

class Security(_message.Message):
    __slots__ = ()
    class SchemesEntry(_message.Message):
        __slots__ = ()
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: StringList
        def __init__(self, key: str | None = ..., value: StringList | _Mapping | None = ...) -> None: ...
    SCHEMES_FIELD_NUMBER: _ClassVar[int]
    schemes: _containers.MessageMap[str, StringList]
    def __init__(self, schemes: _Mapping[str, StringList] | None = ...) -> None: ...

class SecurityScheme(_message.Message):
    __slots__ = ()
    API_KEY_SECURITY_SCHEME_FIELD_NUMBER: _ClassVar[int]
    HTTP_AUTH_SECURITY_SCHEME_FIELD_NUMBER: _ClassVar[int]
    OAUTH2_SECURITY_SCHEME_FIELD_NUMBER: _ClassVar[int]
    OPEN_ID_CONNECT_SECURITY_SCHEME_FIELD_NUMBER: _ClassVar[int]
    MTLS_SECURITY_SCHEME_FIELD_NUMBER: _ClassVar[int]
    api_key_security_scheme: APIKeySecurityScheme
    http_auth_security_scheme: HTTPAuthSecurityScheme
    oauth2_security_scheme: OAuth2SecurityScheme
    open_id_connect_security_scheme: OpenIdConnectSecurityScheme
    mtls_security_scheme: MutualTlsSecurityScheme
    def __init__(self, api_key_security_scheme: APIKeySecurityScheme | _Mapping | None = ..., http_auth_security_scheme: HTTPAuthSecurityScheme | _Mapping | None = ..., oauth2_security_scheme: OAuth2SecurityScheme | _Mapping | None = ..., open_id_connect_security_scheme: OpenIdConnectSecurityScheme | _Mapping | None = ..., mtls_security_scheme: MutualTlsSecurityScheme | _Mapping | None = ...) -> None: ...

class APIKeySecurityScheme(_message.Message):
    __slots__ = ()
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    LOCATION_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    description: str
    location: str
    name: str
    def __init__(self, description: str | None = ..., location: str | None = ..., name: str | None = ...) -> None: ...

class HTTPAuthSecurityScheme(_message.Message):
    __slots__ = ()
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    SCHEME_FIELD_NUMBER: _ClassVar[int]
    BEARER_FORMAT_FIELD_NUMBER: _ClassVar[int]
    description: str
    scheme: str
    bearer_format: str
    def __init__(self, description: str | None = ..., scheme: str | None = ..., bearer_format: str | None = ...) -> None: ...

class OAuth2SecurityScheme(_message.Message):
    __slots__ = ()
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    FLOWS_FIELD_NUMBER: _ClassVar[int]
    OAUTH2_METADATA_URL_FIELD_NUMBER: _ClassVar[int]
    description: str
    flows: OAuthFlows
    oauth2_metadata_url: str
    def __init__(self, description: str | None = ..., flows: OAuthFlows | _Mapping | None = ..., oauth2_metadata_url: str | None = ...) -> None: ...

class OpenIdConnectSecurityScheme(_message.Message):
    __slots__ = ()
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    OPEN_ID_CONNECT_URL_FIELD_NUMBER: _ClassVar[int]
    description: str
    open_id_connect_url: str
    def __init__(self, description: str | None = ..., open_id_connect_url: str | None = ...) -> None: ...

class MutualTlsSecurityScheme(_message.Message):
    __slots__ = ()
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    description: str
    def __init__(self, description: str | None = ...) -> None: ...

class OAuthFlows(_message.Message):
    __slots__ = ()
    AUTHORIZATION_CODE_FIELD_NUMBER: _ClassVar[int]
    CLIENT_CREDENTIALS_FIELD_NUMBER: _ClassVar[int]
    IMPLICIT_FIELD_NUMBER: _ClassVar[int]
    PASSWORD_FIELD_NUMBER: _ClassVar[int]
    authorization_code: AuthorizationCodeOAuthFlow
    client_credentials: ClientCredentialsOAuthFlow
    implicit: ImplicitOAuthFlow
    password: PasswordOAuthFlow
    def __init__(self, authorization_code: AuthorizationCodeOAuthFlow | _Mapping | None = ..., client_credentials: ClientCredentialsOAuthFlow | _Mapping | None = ..., implicit: ImplicitOAuthFlow | _Mapping | None = ..., password: PasswordOAuthFlow | _Mapping | None = ...) -> None: ...

class AuthorizationCodeOAuthFlow(_message.Message):
    __slots__ = ()
    class ScopesEntry(_message.Message):
        __slots__ = ()
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: str | None = ..., value: str | None = ...) -> None: ...
    AUTHORIZATION_URL_FIELD_NUMBER: _ClassVar[int]
    TOKEN_URL_FIELD_NUMBER: _ClassVar[int]
    REFRESH_URL_FIELD_NUMBER: _ClassVar[int]
    SCOPES_FIELD_NUMBER: _ClassVar[int]
    authorization_url: str
    token_url: str
    refresh_url: str
    scopes: _containers.ScalarMap[str, str]
    def __init__(self, authorization_url: str | None = ..., token_url: str | None = ..., refresh_url: str | None = ..., scopes: _Mapping[str, str] | None = ...) -> None: ...

class ClientCredentialsOAuthFlow(_message.Message):
    __slots__ = ()
    class ScopesEntry(_message.Message):
        __slots__ = ()
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: str | None = ..., value: str | None = ...) -> None: ...
    TOKEN_URL_FIELD_NUMBER: _ClassVar[int]
    REFRESH_URL_FIELD_NUMBER: _ClassVar[int]
    SCOPES_FIELD_NUMBER: _ClassVar[int]
    token_url: str
    refresh_url: str
    scopes: _containers.ScalarMap[str, str]
    def __init__(self, token_url: str | None = ..., refresh_url: str | None = ..., scopes: _Mapping[str, str] | None = ...) -> None: ...

class ImplicitOAuthFlow(_message.Message):
    __slots__ = ()
    class ScopesEntry(_message.Message):
        __slots__ = ()
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: str | None = ..., value: str | None = ...) -> None: ...
    AUTHORIZATION_URL_FIELD_NUMBER: _ClassVar[int]
    REFRESH_URL_FIELD_NUMBER: _ClassVar[int]
    SCOPES_FIELD_NUMBER: _ClassVar[int]
    authorization_url: str
    refresh_url: str
    scopes: _containers.ScalarMap[str, str]
    def __init__(self, authorization_url: str | None = ..., refresh_url: str | None = ..., scopes: _Mapping[str, str] | None = ...) -> None: ...

class PasswordOAuthFlow(_message.Message):
    __slots__ = ()
    class ScopesEntry(_message.Message):
        __slots__ = ()
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: str | None = ..., value: str | None = ...) -> None: ...
    TOKEN_URL_FIELD_NUMBER: _ClassVar[int]
    REFRESH_URL_FIELD_NUMBER: _ClassVar[int]
    SCOPES_FIELD_NUMBER: _ClassVar[int]
    token_url: str
    refresh_url: str
    scopes: _containers.ScalarMap[str, str]
    def __init__(self, token_url: str | None = ..., refresh_url: str | None = ..., scopes: _Mapping[str, str] | None = ...) -> None: ...

class SendMessageRequest(_message.Message):
    __slots__ = ()
    REQUEST_FIELD_NUMBER: _ClassVar[int]
    CONFIGURATION_FIELD_NUMBER: _ClassVar[int]
    METADATA_FIELD_NUMBER: _ClassVar[int]
    request: Message
    configuration: SendMessageConfiguration
    metadata: _struct_pb2.Struct
    def __init__(self, request: Message | _Mapping | None = ..., configuration: SendMessageConfiguration | _Mapping | None = ..., metadata: _struct_pb2.Struct | _Mapping | None = ...) -> None: ...

class GetTaskRequest(_message.Message):
    __slots__ = ()
    NAME_FIELD_NUMBER: _ClassVar[int]
    HISTORY_LENGTH_FIELD_NUMBER: _ClassVar[int]
    name: str
    history_length: int
    def __init__(self, name: str | None = ..., history_length: int | None = ...) -> None: ...

class ListTasksRequest(_message.Message):
    __slots__ = ()
    CONTEXT_ID_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    PAGE_SIZE_FIELD_NUMBER: _ClassVar[int]
    PAGE_TOKEN_FIELD_NUMBER: _ClassVar[int]
    HISTORY_LENGTH_FIELD_NUMBER: _ClassVar[int]
    LAST_UPDATED_AFTER_FIELD_NUMBER: _ClassVar[int]
    INCLUDE_ARTIFACTS_FIELD_NUMBER: _ClassVar[int]
    METADATA_FIELD_NUMBER: _ClassVar[int]
    context_id: str
    status: TaskState
    page_size: int
    page_token: str
    history_length: int
    last_updated_after: int
    include_artifacts: bool
    metadata: _struct_pb2.Struct
    def __init__(self, context_id: str | None = ..., status: TaskState | str | None = ..., page_size: int | None = ..., page_token: str | None = ..., history_length: int | None = ..., last_updated_after: int | None = ..., include_artifacts: bool | None = ..., metadata: _struct_pb2.Struct | _Mapping | None = ...) -> None: ...

class ListTasksResponse(_message.Message):
    __slots__ = ()
    TASKS_FIELD_NUMBER: _ClassVar[int]
    NEXT_PAGE_TOKEN_FIELD_NUMBER: _ClassVar[int]
    PAGE_SIZE_FIELD_NUMBER: _ClassVar[int]
    TOTAL_SIZE_FIELD_NUMBER: _ClassVar[int]
    tasks: _containers.RepeatedCompositeFieldContainer[Task]
    next_page_token: str
    page_size: int
    total_size: int
    def __init__(self, tasks: _Iterable[Task | _Mapping] | None = ..., next_page_token: str | None = ..., page_size: int | None = ..., total_size: int | None = ...) -> None: ...

class CancelTaskRequest(_message.Message):
    __slots__ = ()
    NAME_FIELD_NUMBER: _ClassVar[int]
    name: str
    def __init__(self, name: str | None = ...) -> None: ...

class GetTaskPushNotificationConfigRequest(_message.Message):
    __slots__ = ()
    NAME_FIELD_NUMBER: _ClassVar[int]
    name: str
    def __init__(self, name: str | None = ...) -> None: ...

class DeleteTaskPushNotificationConfigRequest(_message.Message):
    __slots__ = ()
    NAME_FIELD_NUMBER: _ClassVar[int]
    name: str
    def __init__(self, name: str | None = ...) -> None: ...

class SetTaskPushNotificationConfigRequest(_message.Message):
    __slots__ = ()
    PARENT_FIELD_NUMBER: _ClassVar[int]
    CONFIG_ID_FIELD_NUMBER: _ClassVar[int]
    CONFIG_FIELD_NUMBER: _ClassVar[int]
    parent: str
    config_id: str
    config: TaskPushNotificationConfig
    def __init__(self, parent: str | None = ..., config_id: str | None = ..., config: TaskPushNotificationConfig | _Mapping | None = ...) -> None: ...

class SubscribeToTaskRequest(_message.Message):
    __slots__ = ()
    NAME_FIELD_NUMBER: _ClassVar[int]
    name: str
    def __init__(self, name: str | None = ...) -> None: ...

class ListTaskPushNotificationConfigRequest(_message.Message):
    __slots__ = ()
    PARENT_FIELD_NUMBER: _ClassVar[int]
    PAGE_SIZE_FIELD_NUMBER: _ClassVar[int]
    PAGE_TOKEN_FIELD_NUMBER: _ClassVar[int]
    parent: str
    page_size: int
    page_token: str
    def __init__(self, parent: str | None = ..., page_size: int | None = ..., page_token: str | None = ...) -> None: ...

class GetExtendedAgentCardRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class SendMessageResponse(_message.Message):
    __slots__ = ()
    TASK_FIELD_NUMBER: _ClassVar[int]
    MSG_FIELD_NUMBER: _ClassVar[int]
    task: Task
    msg: Message
    def __init__(self, task: Task | _Mapping | None = ..., msg: Message | _Mapping | None = ...) -> None: ...

class StreamResponse(_message.Message):
    __slots__ = ()
    TASK_FIELD_NUMBER: _ClassVar[int]
    MSG_FIELD_NUMBER: _ClassVar[int]
    STATUS_UPDATE_FIELD_NUMBER: _ClassVar[int]
    ARTIFACT_UPDATE_FIELD_NUMBER: _ClassVar[int]
    task: Task
    msg: Message
    status_update: TaskStatusUpdateEvent
    artifact_update: TaskArtifactUpdateEvent
    def __init__(self, task: Task | _Mapping | None = ..., msg: Message | _Mapping | None = ..., status_update: TaskStatusUpdateEvent | _Mapping | None = ..., artifact_update: TaskArtifactUpdateEvent | _Mapping | None = ...) -> None: ...

class ListTaskPushNotificationConfigResponse(_message.Message):
    __slots__ = ()
    CONFIGS_FIELD_NUMBER: _ClassVar[int]
    NEXT_PAGE_TOKEN_FIELD_NUMBER: _ClassVar[int]
    configs: _containers.RepeatedCompositeFieldContainer[TaskPushNotificationConfig]
    next_page_token: str
    def __init__(self, configs: _Iterable[TaskPushNotificationConfig | _Mapping] | None = ..., next_page_token: str | None = ...) -> None: ...
