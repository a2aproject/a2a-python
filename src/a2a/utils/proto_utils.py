# mypy: disable-error-code="arg-type"
"""Utils for converting between proto and Python types."""

import json
import logging
import re

from typing import Any

from google.protobuf import json_format, struct_pb2

from a2a import types
from a2a.grpc import a2a_pb2
from a2a.utils.errors import ServerError


logger = logging.getLogger(__name__)


# Regexp patterns for matching
_TASK_NAME_MATCH = re.compile(r'tasks/([^/]+)')
_TASK_PUSH_CONFIG_NAME_MATCH = re.compile(
    r'tasks/([^/]+)/pushNotificationConfigs/([^/]+)'
)


def dict_to_struct(dictionary: dict[str, Any]) -> struct_pb2.Struct:
    """Converts a Python dict to a Struct proto.

    Unfortunately, using `json_format.ParseDict` does not work because this
    wants the dictionary to be an exact match of the Struct proto with fields
    and keys and values, not the traditional Python dict structure.

    Args:
      dictionary: The Python dict to convert.

    Returns:
      The Struct proto.
    """
    struct = struct_pb2.Struct()
    for key, val in dictionary.items():
        if isinstance(val, dict):
            struct[key] = dict_to_struct(val)
        else:
            struct[key] = val
    return struct


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


class ToProto:
    """Converts Python types to proto types."""

    @classmethod
    def message(cls, message: types.Message | None) -> a2a_pb2.Message | None:
        if message is None:
            return None
        return a2a_pb2.Message(
            message_id=message.message_id,
            content=[cls.part(p) for p in message.parts],
            context_id=message.context_id or '',
            task_id=message.task_id or '',
            role=cls.role(message.role),
            metadata=cls.metadata(message.metadata),
            extensions=message.extensions or [],
        )

    @classmethod
    def metadata(
        cls, metadata: dict[str, Any] | None
    ) -> struct_pb2.Struct | None:
        if metadata is None:
            return None
        return dict_to_struct(metadata)

    @classmethod
    def part(cls, part: types.Part) -> a2a_pb2.Part:
        if isinstance(part.root, types.TextPart):
            return a2a_pb2.Part(
                text=part.root.text, metadata=cls.metadata(part.root.metadata)
            )
        if isinstance(part.root, types.FilePart):
            return a2a_pb2.Part(
                file=cls.file(part.root.file),
                metadata=cls.metadata(part.root.metadata),
            )
        if isinstance(part.root, types.DataPart):
            return a2a_pb2.Part(
                data=cls.data(part.root.data),
                metadata=cls.metadata(part.root.metadata),
            )
        raise ValueError(f'Unsupported part type: {part.root}')

    @classmethod
    def data(cls, data: dict[str, Any]) -> a2a_pb2.DataPart:
        return a2a_pb2.DataPart(data=dict_to_struct(data))

    @classmethod
    def file(
        cls, file: types.FileWithUri | types.FileWithBytes
    ) -> a2a_pb2.FilePart:
        if isinstance(file, types.FileWithUri):
            return a2a_pb2.FilePart(
                file_with_uri=file.uri, mime_type=file.mime_type, name=file.name
            )
        return a2a_pb2.FilePart(
            file_with_bytes=file.bytes.encode('utf-8'),
            mime_type=file.mime_type,
            name=file.name,
        )

    @classmethod
    def task(cls, task: types.Task) -> a2a_pb2.Task:
        return a2a_pb2.Task(
            id=task.id,
            context_id=task.context_id,
            status=cls.task_status(task.status),
            artifacts=(
                [cls.artifact(a) for a in task.artifacts]
                if task.artifacts
                else None
            ),
            history=(
                [cls.message(h) for h in task.history]  # type: ignore[misc]
                if task.history
                else None
            ),
            metadata=cls.metadata(task.metadata),
        )

    @classmethod
    def task_status(cls, status: types.TaskStatus) -> a2a_pb2.TaskStatus:
        return a2a_pb2.TaskStatus(
            state=cls.task_state(status.state),
            update=cls.message(status.message),
        )

    @classmethod
    def task_state(cls, state: types.TaskState) -> a2a_pb2.TaskState:
        match state:
            case types.TaskState.submitted:
                return a2a_pb2.TaskState.TASK_STATE_SUBMITTED
            case types.TaskState.working:
                return a2a_pb2.TaskState.TASK_STATE_WORKING
            case types.TaskState.completed:
                return a2a_pb2.TaskState.TASK_STATE_COMPLETED
            case types.TaskState.canceled:
                return a2a_pb2.TaskState.TASK_STATE_CANCELLED
            case types.TaskState.failed:
                return a2a_pb2.TaskState.TASK_STATE_FAILED
            case types.TaskState.input_required:
                return a2a_pb2.TaskState.TASK_STATE_INPUT_REQUIRED
            case types.TaskState.auth_required:
                return a2a_pb2.TaskState.TASK_STATE_AUTH_REQUIRED
            case _:
                return a2a_pb2.TaskState.TASK_STATE_UNSPECIFIED

    @classmethod
    def artifact(cls, artifact: types.Artifact) -> a2a_pb2.Artifact:
        return a2a_pb2.Artifact(
            artifact_id=artifact.artifact_id,
            description=artifact.description,
            metadata=cls.metadata(artifact.metadata),
            name=artifact.name,
            parts=[cls.part(p) for p in artifact.parts],
            extensions=artifact.extensions or [],
        )

    @classmethod
    def authentication_info(
        cls, info: types.PushNotificationAuthenticationInfo
    ) -> a2a_pb2.AuthenticationInfo:
        return a2a_pb2.AuthenticationInfo(
            schemes=info.schemes,
            credentials=info.credentials,
        )

    @classmethod
    def push_notification_config(
        cls, config: types.PushNotificationConfig
    ) -> a2a_pb2.PushNotificationConfig:
        auth_info = (
            cls.authentication_info(config.authentication)
            if config.authentication
            else None
        )
        return a2a_pb2.PushNotificationConfig(
            id=config.id or '',
            url=config.url,
            token=config.token,
            authentication=auth_info,
        )

    @classmethod
    def task_artifact_update_event(
        cls, event: types.TaskArtifactUpdateEvent
    ) -> a2a_pb2.TaskArtifactUpdateEvent:
        return a2a_pb2.TaskArtifactUpdateEvent(
            task_id=event.task_id,
            context_id=event.context_id,
            artifact=cls.artifact(event.artifact),
            metadata=cls.metadata(event.metadata),
            append=event.append or False,
            last_chunk=event.last_chunk or False,
        )

    @classmethod
    def task_status_update_event(
        cls, event: types.TaskStatusUpdateEvent
    ) -> a2a_pb2.TaskStatusUpdateEvent:
        return a2a_pb2.TaskStatusUpdateEvent(
            task_id=event.task_id,
            context_id=event.context_id,
            status=cls.task_status(event.status),
            metadata=cls.metadata(event.metadata),
            final=event.final,
        )

    @classmethod
    def message_send_configuration(
        cls, config: types.MessageSendConfiguration | None
    ) -> a2a_pb2.SendMessageConfiguration:
        if not config:
            return a2a_pb2.SendMessageConfiguration()
        return a2a_pb2.SendMessageConfiguration(
            accepted_output_modes=config.accepted_output_modes,
            push_notification=cls.push_notification_config(
                config.push_notification_config
            )
            if config.push_notification_config
            else None,
            history_length=config.history_length,
            blocking=config.blocking or False,
        )

    @classmethod
    def update_event(
        cls,
        event: types.Task
        | types.Message
        | types.TaskStatusUpdateEvent
        | types.TaskArtifactUpdateEvent,
    ) -> a2a_pb2.StreamResponse:
        """Converts a task, message, or task update event to a StreamResponse."""
        return cls.stream_response(event)

    @classmethod
    def task_or_message(
        cls, event: types.Task | types.Message
    ) -> a2a_pb2.SendMessageResponse:
        if isinstance(event, types.Message):
            return a2a_pb2.SendMessageResponse(
                msg=cls.message(event),
            )
        return a2a_pb2.SendMessageResponse(
            task=cls.task(event),
        )

    @classmethod
    def stream_response(
        cls,
        event: (
            types.Message
            | types.Task
            | types.TaskStatusUpdateEvent
            | types.TaskArtifactUpdateEvent
        ),
    ) -> a2a_pb2.StreamResponse:
        if isinstance(event, types.Message):
            return a2a_pb2.StreamResponse(msg=cls.message(event))
        if isinstance(event, types.Task):
            return a2a_pb2.StreamResponse(task=cls.task(event))
        if isinstance(event, types.TaskStatusUpdateEvent):
            return a2a_pb2.StreamResponse(
                status_update=cls.task_status_update_event(event),
            )
        if isinstance(event, types.TaskArtifactUpdateEvent):
            return a2a_pb2.StreamResponse(
                artifact_update=cls.task_artifact_update_event(event),
            )
        raise ValueError(f'Unsupported event type: {type(event)}')

    @classmethod
    def task_push_notification_config(
        cls, config: types.TaskPushNotificationConfig
    ) -> a2a_pb2.TaskPushNotificationConfig:
        return a2a_pb2.TaskPushNotificationConfig(
            name=f'tasks/{config.task_id}/pushNotificationConfigs/{config.push_notification_config.id}',
            push_notification_config=cls.push_notification_config(
                config.push_notification_config,
            ),
        )

    @classmethod
    def agent_card(
        cls,
        card: types.AgentCard,
    ) -> a2a_pb2.AgentCard:
        return a2a_pb2.AgentCard(
            capabilities=cls.capabilities(card.capabilities),
            default_input_modes=list(card.default_input_modes),
            default_output_modes=list(card.default_output_modes),
            description=card.description,
            documentation_url=card.documentation_url,
            name=card.name,
            provider=cls.provider(card.provider),
            security=cls.security(card.security),
            security_schemes=cls.security_schemes(card.security_schemes),
            skills=[cls.skill(x) for x in card.skills] if card.skills else [],
            url=card.url,
            version=card.version,
            supports_authenticated_extended_card=bool(
                card.supports_authenticated_extended_card
            ),
            preferred_transport=card.preferred_transport,
            protocol_version=card.protocol_version,
            additional_interfaces=[
                cls.agent_interface(x) for x in card.additional_interfaces
            ]
            if card.additional_interfaces
            else None,
            signatures=[cls.agent_card_signature(x) for x in card.signatures]
            if card.signatures
            else None,
        )

    @classmethod
    def agent_card_signature(
        cls, signature: types.AgentCardSignature
    ) -> a2a_pb2.AgentCardSignature:
        return a2a_pb2.AgentCardSignature(
            protected=signature.protected,
            signature=signature.signature,
            header=dict_to_struct(signature.header)
            if signature.header is not None
            else None,
        )

    @classmethod
    def agent_interface(
        cls,
        interface: types.AgentInterface,
    ) -> a2a_pb2.AgentInterface:
        return a2a_pb2.AgentInterface(
            transport=interface.transport,
            url=interface.url,
        )

    @classmethod
    def capabilities(
        cls, capabilities: types.AgentCapabilities
    ) -> a2a_pb2.AgentCapabilities:
        return a2a_pb2.AgentCapabilities(
            streaming=bool(capabilities.streaming),
            push_notifications=bool(capabilities.push_notifications),
            extensions=[
                cls.extension(x) for x in capabilities.extensions or []
            ],
        )

    @classmethod
    def extension(
        cls,
        extension: types.AgentExtension,
    ) -> a2a_pb2.AgentExtension:
        return a2a_pb2.AgentExtension(
            uri=extension.uri,
            description=extension.description,
            params=dict_to_struct(extension.params)
            if extension.params
            else None,
            required=extension.required,
        )

    @classmethod
    def provider(
        cls, provider: types.AgentProvider | None
    ) -> a2a_pb2.AgentProvider | None:
        if not provider:
            return None
        return a2a_pb2.AgentProvider(
            organization=provider.organization,
            url=provider.url,
        )

    @classmethod
    def security(
        cls,
        security: list[dict[str, list[str]]] | None,
    ) -> list[a2a_pb2.Security] | None:
        if not security:
            return None
        return [
            a2a_pb2.Security(
                schemes={k: a2a_pb2.StringList(list=v) for (k, v) in s.items()}
            )
            for s in security
        ]

    @classmethod
    def security_schemes(
        cls,
        schemes: dict[str, types.SecurityScheme] | None,
    ) -> dict[str, a2a_pb2.SecurityScheme] | None:
        if not schemes:
            return None
        return {k: cls.security_scheme(v) for (k, v) in schemes.items()}

    @classmethod
    def security_scheme(
        cls,
        scheme: types.SecurityScheme,
    ) -> a2a_pb2.SecurityScheme:
        if isinstance(scheme.root, types.APIKeySecurityScheme):
            return a2a_pb2.SecurityScheme(
                api_key_security_scheme=a2a_pb2.APIKeySecurityScheme(
                    description=scheme.root.description,
                    location=scheme.root.in_.value,
                    name=scheme.root.name,
                )
            )
        if isinstance(scheme.root, types.HTTPAuthSecurityScheme):
            return a2a_pb2.SecurityScheme(
                http_auth_security_scheme=a2a_pb2.HTTPAuthSecurityScheme(
                    description=scheme.root.description,
                    scheme=scheme.root.scheme,
                    bearer_format=scheme.root.bearer_format,
                )
            )
        if isinstance(scheme.root, types.OAuth2SecurityScheme):
            return a2a_pb2.SecurityScheme(
                oauth2_security_scheme=a2a_pb2.OAuth2SecurityScheme(
                    description=scheme.root.description,
                    flows=cls.oauth2_flows(scheme.root.flows),
                )
            )
        if isinstance(scheme.root, types.MutualTLSSecurityScheme):
            return a2a_pb2.SecurityScheme(
                mtls_security_scheme=a2a_pb2.MutualTlsSecurityScheme(
                    description=scheme.root.description,
                )
            )
        return a2a_pb2.SecurityScheme(
            open_id_connect_security_scheme=a2a_pb2.OpenIdConnectSecurityScheme(
                description=scheme.root.description,
                open_id_connect_url=scheme.root.open_id_connect_url,
            )
        )

    @classmethod
    def oauth2_flows(cls, flows: types.OAuthFlows) -> a2a_pb2.OAuthFlows:
        if flows.authorization_code:
            return a2a_pb2.OAuthFlows(
                authorization_code=a2a_pb2.AuthorizationCodeOAuthFlow(
                    authorization_url=flows.authorization_code.authorization_url,
                    refresh_url=flows.authorization_code.refresh_url,
                    scopes=dict(flows.authorization_code.scopes.items()),
                    token_url=flows.authorization_code.token_url,
                ),
            )
        if flows.client_credentials:
            return a2a_pb2.OAuthFlows(
                client_credentials=a2a_pb2.ClientCredentialsOAuthFlow(
                    refresh_url=flows.client_credentials.refresh_url,
                    scopes=dict(flows.client_credentials.scopes.items()),
                    token_url=flows.client_credentials.token_url,
                ),
            )
        if flows.implicit:
            return a2a_pb2.OAuthFlows(
                implicit=a2a_pb2.ImplicitOAuthFlow(
                    authorization_url=flows.implicit.authorization_url,
                    refresh_url=flows.implicit.refresh_url,
                    scopes=dict(flows.implicit.scopes.items()),
                ),
            )
        if flows.password:
            return a2a_pb2.OAuthFlows(
                password=a2a_pb2.PasswordOAuthFlow(
                    refresh_url=flows.password.refresh_url,
                    scopes=dict(flows.password.scopes.items()),
                    token_url=flows.password.token_url,
                ),
            )
        raise ValueError('Unknown oauth flow definition')

    @classmethod
    def skill(cls, skill: types.AgentSkill) -> a2a_pb2.AgentSkill:
        return a2a_pb2.AgentSkill(
            id=skill.id,
            name=skill.name,
            description=skill.description,
            tags=skill.tags,
            examples=skill.examples,
            input_modes=skill.input_modes,
            output_modes=skill.output_modes,
        )

    @classmethod
    def role(cls, role: types.Role) -> a2a_pb2.Role:
        match role:
            case types.Role.user:
                return a2a_pb2.Role.ROLE_USER
            case types.Role.agent:
                return a2a_pb2.Role.ROLE_AGENT
            case _:
                return a2a_pb2.Role.ROLE_UNSPECIFIED


class FromProto:
    """Converts proto types to Python types."""

    @classmethod
    def message(cls, message: a2a_pb2.Message) -> types.Message:
        return types.Message(
            message_id=message.message_id,
            parts=[cls.part(p) for p in message.content],
            context_id=message.context_id or None,
            task_id=message.task_id or None,
            role=cls.role(message.role),
            metadata=cls.metadata(message.metadata),
            extensions=list(message.extensions) or None,
        )

    @classmethod
    def metadata(cls, metadata: struct_pb2.Struct) -> dict[str, Any]:
        if not metadata.fields:
            return {}
        return json_format.MessageToDict(metadata)

    @classmethod
    def part(cls, part: a2a_pb2.Part) -> types.Part:
        if part.HasField('text'):
            return types.Part(
                root=types.TextPart(
                    text=part.text,
                    metadata=cls.metadata(part.metadata)
                    if part.metadata
                    else None,
                ),
            )
        if part.HasField('file'):
            return types.Part(
                root=types.FilePart(
                    file=cls.file(part.file),
                    metadata=cls.metadata(part.metadata)
                    if part.metadata
                    else None,
                ),
            )
        if part.HasField('data'):
            return types.Part(
                root=types.DataPart(
                    data=cls.data(part.data),
                    metadata=cls.metadata(part.metadata)
                    if part.metadata
                    else None,
                ),
            )
        raise ValueError(f'Unsupported part type: {part}')

    @classmethod
    def data(cls, data: a2a_pb2.DataPart) -> dict[str, Any]:
        json_data = json_format.MessageToJson(data.data)
        return json.loads(json_data)

    @classmethod
    def file(
        cls, file: a2a_pb2.FilePart
    ) -> types.FileWithUri | types.FileWithBytes:
        common_args = {
            'mime_type': file.mime_type or None,
            'name': file.name or None,
        }
        if file.HasField('file_with_uri'):
            return types.FileWithUri(
                uri=file.file_with_uri,
                **common_args,
            )
        return types.FileWithBytes(
            bytes=file.file_with_bytes.decode('utf-8'),
            **common_args,
        )

    @classmethod
    def task_or_message(
        cls, event: a2a_pb2.SendMessageResponse
    ) -> types.Task | types.Message:
        if event.HasField('msg'):
            return cls.message(event.msg)
        return cls.task(event.task)

    @classmethod
    def task(cls, task: a2a_pb2.Task) -> types.Task:
        return types.Task(
            id=task.id,
            context_id=task.context_id,
            status=cls.task_status(task.status),
            artifacts=[cls.artifact(a) for a in task.artifacts],
            history=[cls.message(h) for h in task.history],
            metadata=cls.metadata(task.metadata),
        )

    @classmethod
    def task_status(cls, status: a2a_pb2.TaskStatus) -> types.TaskStatus:
        return types.TaskStatus(
            state=cls.task_state(status.state),
            message=cls.message(status.update),
        )

    @classmethod
    def task_state(cls, state: a2a_pb2.TaskState) -> types.TaskState:
        match state:
            case a2a_pb2.TaskState.TASK_STATE_SUBMITTED:
                return types.TaskState.submitted
            case a2a_pb2.TaskState.TASK_STATE_WORKING:
                return types.TaskState.working
            case a2a_pb2.TaskState.TASK_STATE_COMPLETED:
                return types.TaskState.completed
            case a2a_pb2.TaskState.TASK_STATE_CANCELLED:
                return types.TaskState.canceled
            case a2a_pb2.TaskState.TASK_STATE_FAILED:
                return types.TaskState.failed
            case a2a_pb2.TaskState.TASK_STATE_INPUT_REQUIRED:
                return types.TaskState.input_required
            case a2a_pb2.TaskState.TASK_STATE_AUTH_REQUIRED:
                return types.TaskState.auth_required
            case _:
                return types.TaskState.unknown

    @classmethod
    def artifact(cls, artifact: a2a_pb2.Artifact) -> types.Artifact:
        return types.Artifact(
            artifact_id=artifact.artifact_id,
            description=artifact.description,
            metadata=cls.metadata(artifact.metadata),
            name=artifact.name,
            parts=[cls.part(p) for p in artifact.parts],
            extensions=artifact.extensions or None,
        )

    @classmethod
    def task_artifact_update_event(
        cls, event: a2a_pb2.TaskArtifactUpdateEvent
    ) -> types.TaskArtifactUpdateEvent:
        return types.TaskArtifactUpdateEvent(
            task_id=event.task_id,
            context_id=event.context_id,
            artifact=cls.artifact(event.artifact),
            metadata=cls.metadata(event.metadata),
            append=event.append,
            last_chunk=event.last_chunk,
        )

    @classmethod
    def task_status_update_event(
        cls, event: a2a_pb2.TaskStatusUpdateEvent
    ) -> types.TaskStatusUpdateEvent:
        return types.TaskStatusUpdateEvent(
            task_id=event.task_id,
            context_id=event.context_id,
            status=cls.task_status(event.status),
            metadata=cls.metadata(event.metadata),
            final=event.final,
        )

    @classmethod
    def push_notification_config(
        cls, config: a2a_pb2.PushNotificationConfig
    ) -> types.PushNotificationConfig:
        return types.PushNotificationConfig(
            id=config.id,
            url=config.url,
            token=config.token,
            authentication=cls.authentication_info(config.authentication)
            if config.HasField('authentication')
            else None,
        )

    @classmethod
    def authentication_info(
        cls, info: a2a_pb2.AuthenticationInfo
    ) -> types.PushNotificationAuthenticationInfo:
        return types.PushNotificationAuthenticationInfo(
            schemes=list(info.schemes),
            credentials=info.credentials,
        )

    @classmethod
    def message_send_configuration(
        cls, config: a2a_pb2.SendMessageConfiguration
    ) -> types.MessageSendConfiguration:
        return types.MessageSendConfiguration(
            accepted_output_modes=list(config.accepted_output_modes),
            push_notification_config=cls.push_notification_config(
                config.push_notification
            )
            if config.HasField('push_notification')
            else None,
            history_length=config.history_length,
            blocking=config.blocking,
        )

    @classmethod
    def message_send_params(
        cls, request: a2a_pb2.SendMessageRequest
    ) -> types.MessageSendParams:
        return types.MessageSendParams(
            configuration=cls.message_send_configuration(request.configuration),
            message=cls.message(request.request),
            metadata=cls.metadata(request.metadata),
        )

    @classmethod
    def task_id_params(
        cls,
        request: (
            a2a_pb2.CancelTaskRequest
            | a2a_pb2.TaskSubscriptionRequest
            | a2a_pb2.GetTaskPushNotificationConfigRequest
        ),
    ) -> types.TaskIdParams:
        if isinstance(request, a2a_pb2.GetTaskPushNotificationConfigRequest):
            m = _TASK_PUSH_CONFIG_NAME_MATCH.match(request.name)
            if not m:
                raise ServerError(
                    error=types.InvalidParamsError(
                        message=f'No task for {request.name}'
                    )
                )
            return types.TaskIdParams(id=m.group(1))
        m = _TASK_NAME_MATCH.match(request.name)
        if not m:
            raise ServerError(
                error=types.InvalidParamsError(
                    message=f'No task for {request.name}'
                )
            )
        return types.TaskIdParams(id=m.group(1))

    @classmethod
    def task_push_notification_config_request(
        cls,
        request: a2a_pb2.CreateTaskPushNotificationConfigRequest,
    ) -> types.TaskPushNotificationConfig:
        m = _TASK_NAME_MATCH.match(request.parent)
        if not m:
            raise ServerError(
                error=types.InvalidParamsError(
                    message=f'No task for {request.parent}'
                )
            )
        return types.TaskPushNotificationConfig(
            push_notification_config=cls.push_notification_config(
                request.config.push_notification_config,
            ),
            task_id=m.group(1),
        )

    @classmethod
    def task_push_notification_config(
        cls,
        config: a2a_pb2.TaskPushNotificationConfig,
    ) -> types.TaskPushNotificationConfig:
        m = _TASK_PUSH_CONFIG_NAME_MATCH.match(config.name)
        if not m:
            raise ServerError(
                error=types.InvalidParamsError(
                    message=f'Bad TaskPushNotificationConfig resource name {config.name}'
                )
            )
        return types.TaskPushNotificationConfig(
            push_notification_config=cls.push_notification_config(
                config.push_notification_config,
            ),
            task_id=m.group(1),
        )

    @classmethod
    def agent_card(
        cls,
        card: a2a_pb2.AgentCard,
    ) -> types.AgentCard:
        return types.AgentCard(
            capabilities=cls.capabilities(card.capabilities),
            default_input_modes=list(card.default_input_modes),
            default_output_modes=list(card.default_output_modes),
            description=card.description,
            documentation_url=card.documentation_url,
            name=card.name,
            provider=cls.provider(card.provider),
            security=cls.security(list(card.security)),
            security_schemes=cls.security_schemes(dict(card.security_schemes)),
            skills=[cls.skill(x) for x in card.skills] if card.skills else [],
            url=card.url,
            version=card.version,
            supports_authenticated_extended_card=card.supports_authenticated_extended_card,
            preferred_transport=card.preferred_transport,
            protocol_version=card.protocol_version,
            additional_interfaces=[
                cls.agent_interface(x) for x in card.additional_interfaces
            ]
            if card.additional_interfaces
            else None,
            signatures=[cls.agent_card_signature(x) for x in card.signatures]
            if card.signatures
            else None,
        )

    @classmethod
    def agent_card_signature(
        cls, signature: a2a_pb2.AgentCardSignature
    ) -> types.AgentCardSignature:
        return types.AgentCardSignature(
            protected=signature.protected,
            signature=signature.signature,
            header=json_format.MessageToDict(signature.header),
        )

    @classmethod
    def agent_interface(
        cls,
        interface: a2a_pb2.AgentInterface,
    ) -> types.AgentInterface:
        return types.AgentInterface(
            transport=interface.transport,
            url=interface.url,
        )

    @classmethod
    def task_query_params(
        cls,
        request: a2a_pb2.GetTaskRequest,
    ) -> types.TaskQueryParams:
        m = _TASK_NAME_MATCH.match(request.name)
        if not m:
            raise ServerError(
                error=types.InvalidParamsError(
                    message=f'No task for {request.name}'
                )
            )
        return types.TaskQueryParams(
            history_length=request.history_length
            if request.history_length
            else None,
            id=m.group(1),
            metadata=None,
        )

    @classmethod
    def capabilities(
        cls, capabilities: a2a_pb2.AgentCapabilities
    ) -> types.AgentCapabilities:
        return types.AgentCapabilities(
            streaming=capabilities.streaming,
            push_notifications=capabilities.push_notifications,
            extensions=[
                cls.agent_extension(x) for x in capabilities.extensions
            ],
        )

    @classmethod
    def agent_extension(
        cls,
        extension: a2a_pb2.AgentExtension,
    ) -> types.AgentExtension:
        return types.AgentExtension(
            uri=extension.uri,
            description=extension.description,
            params=json_format.MessageToDict(extension.params),
            required=extension.required,
        )

    @classmethod
    def security(
        cls,
        security: list[a2a_pb2.Security] | None,
    ) -> list[dict[str, list[str]]] | None:
        if not security:
            return None
        return [
            {k: list(v.list) for (k, v) in s.schemes.items()} for s in security
        ]

    @classmethod
    def provider(
        cls, provider: a2a_pb2.AgentProvider | None
    ) -> types.AgentProvider | None:
        if not provider:
            return None
        return types.AgentProvider(
            organization=provider.organization,
            url=provider.url,
        )

    @classmethod
    def security_schemes(
        cls, schemes: dict[str, a2a_pb2.SecurityScheme]
    ) -> dict[str, types.SecurityScheme]:
        return {k: cls.security_scheme(v) for (k, v) in schemes.items()}

    @classmethod
    def security_scheme(
        cls,
        scheme: a2a_pb2.SecurityScheme,
    ) -> types.SecurityScheme:
        if scheme.HasField('api_key_security_scheme'):
            return types.SecurityScheme(
                root=types.APIKeySecurityScheme(
                    description=scheme.api_key_security_scheme.description,
                    name=scheme.api_key_security_scheme.name,
                    in_=types.In(scheme.api_key_security_scheme.location),  # type: ignore[call-arg]
                )
            )
        if scheme.HasField('http_auth_security_scheme'):
            return types.SecurityScheme(
                root=types.HTTPAuthSecurityScheme(
                    description=scheme.http_auth_security_scheme.description,
                    scheme=scheme.http_auth_security_scheme.scheme,
                    bearer_format=scheme.http_auth_security_scheme.bearer_format,
                )
            )
        if scheme.HasField('oauth2_security_scheme'):
            return types.SecurityScheme(
                root=types.OAuth2SecurityScheme(
                    description=scheme.oauth2_security_scheme.description,
                    flows=cls.oauth2_flows(scheme.oauth2_security_scheme.flows),
                )
            )
        if scheme.HasField('mtls_security_scheme'):
            return types.SecurityScheme(
                root=types.MutualTLSSecurityScheme(
                    description=scheme.mtls_security_scheme.description,
                )
            )
        return types.SecurityScheme(
            root=types.OpenIdConnectSecurityScheme(
                description=scheme.open_id_connect_security_scheme.description,
                open_id_connect_url=scheme.open_id_connect_security_scheme.open_id_connect_url,
            )
        )

    @classmethod
    def oauth2_flows(cls, flows: a2a_pb2.OAuthFlows) -> types.OAuthFlows:
        if flows.HasField('authorization_code'):
            return types.OAuthFlows(
                authorization_code=types.AuthorizationCodeOAuthFlow(
                    authorization_url=flows.authorization_code.authorization_url,
                    refresh_url=flows.authorization_code.refresh_url,
                    scopes=dict(flows.authorization_code.scopes.items()),
                    token_url=flows.authorization_code.token_url,
                ),
            )
        if flows.HasField('client_credentials'):
            return types.OAuthFlows(
                client_credentials=types.ClientCredentialsOAuthFlow(
                    refresh_url=flows.client_credentials.refresh_url,
                    scopes=dict(flows.client_credentials.scopes.items()),
                    token_url=flows.client_credentials.token_url,
                ),
            )
        if flows.HasField('implicit'):
            return types.OAuthFlows(
                implicit=types.ImplicitOAuthFlow(
                    authorization_url=flows.implicit.authorization_url,
                    refresh_url=flows.implicit.refresh_url,
                    scopes=dict(flows.implicit.scopes.items()),
                ),
            )
        return types.OAuthFlows(
            password=types.PasswordOAuthFlow(
                refresh_url=flows.password.refresh_url,
                scopes=dict(flows.password.scopes.items()),
                token_url=flows.password.token_url,
            ),
        )

    @classmethod
    def stream_response(
        cls,
        response: a2a_pb2.StreamResponse,
    ) -> (
        types.Message
        | types.Task
        | types.TaskStatusUpdateEvent
        | types.TaskArtifactUpdateEvent
    ):
        if response.HasField('msg'):
            return cls.message(response.msg)
        if response.HasField('task'):
            return cls.task(response.task)
        if response.HasField('status_update'):
            return cls.task_status_update_event(response.status_update)
        if response.HasField('artifact_update'):
            return cls.task_artifact_update_event(response.artifact_update)
        raise ValueError('Unsupported StreamResponse type')

    @classmethod
    def skill(cls, skill: a2a_pb2.AgentSkill) -> types.AgentSkill:
        return types.AgentSkill(
            id=skill.id,
            name=skill.name,
            description=skill.description,
            tags=list(skill.tags),
            examples=list(skill.examples),
            input_modes=list(skill.input_modes),
            output_modes=list(skill.output_modes),
        )

    @classmethod
    def role(cls, role: a2a_pb2.Role) -> types.Role:
        match role:
            case a2a_pb2.Role.ROLE_USER:
                return types.Role.user
            case a2a_pb2.Role.ROLE_AGENT:
                return types.Role.agent
            case _:
                return types.Role.agent
