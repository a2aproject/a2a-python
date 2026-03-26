import base64

from typing import Any

from google.protobuf.json_format import MessageToDict, ParseDict

from a2a.compat.v0_3 import types as types_v03
from a2a.compat.v0_3.versions import is_legacy_version
from a2a.types import a2a_pb2 as pb2_v10
from a2a.utils import constants, errors


_COMPAT_TO_CORE_TASK_STATE: dict[types_v03.TaskState, Any] = {
    types_v03.TaskState.unknown: pb2_v10.TaskState.TASK_STATE_UNSPECIFIED,
    types_v03.TaskState.submitted: pb2_v10.TaskState.TASK_STATE_SUBMITTED,
    types_v03.TaskState.working: pb2_v10.TaskState.TASK_STATE_WORKING,
    types_v03.TaskState.completed: pb2_v10.TaskState.TASK_STATE_COMPLETED,
    types_v03.TaskState.failed: pb2_v10.TaskState.TASK_STATE_FAILED,
    types_v03.TaskState.canceled: pb2_v10.TaskState.TASK_STATE_CANCELED,
    types_v03.TaskState.input_required: pb2_v10.TaskState.TASK_STATE_INPUT_REQUIRED,
    types_v03.TaskState.rejected: pb2_v10.TaskState.TASK_STATE_REJECTED,
    types_v03.TaskState.auth_required: pb2_v10.TaskState.TASK_STATE_AUTH_REQUIRED,
}

_CORE_TO_COMPAT_TASK_STATE: dict[Any, types_v03.TaskState] = {
    v: k for k, v in _COMPAT_TO_CORE_TASK_STATE.items()
}


def to_core_part(compat_part: types_v03.Part) -> pb2_v10.Part:  # noqa: PLR0912
    """Converts a v0.3 Part (Pydantic model) to a v1.0 core Part (Protobuf object)."""
    core_part = pb2_v10.Part()
    root = compat_part.root

    if isinstance(root, types_v03.TextPart):
        core_part.text = root.text
        if root.metadata is not None:
            ParseDict(root.metadata, core_part.metadata)

    elif isinstance(root, types_v03.DataPart):
        if root.metadata is None:
            data_part_compat = False
        else:
            meta = dict(root.metadata)
            data_part_compat = meta.pop('data_part_compat', False)
            if meta:
                ParseDict(meta, core_part.metadata)

        if data_part_compat:
            val = root.data['value']
            ParseDict(val, core_part.data)
        else:
            ParseDict(root.data, core_part.data.struct_value)

    elif isinstance(root, types_v03.FilePart):
        if isinstance(root.file, types_v03.FileWithBytes):
            core_part.raw = base64.b64decode(root.file.bytes)
            if root.file.mime_type:
                core_part.media_type = root.file.mime_type
            if root.file.name:
                core_part.filename = root.file.name
        elif isinstance(root.file, types_v03.FileWithUri):
            core_part.url = root.file.uri
            if root.file.mime_type:
                core_part.media_type = root.file.mime_type
            if root.file.name:
                core_part.filename = root.file.name

        if root.metadata is not None:
            ParseDict(root.metadata, core_part.metadata)

    return core_part


def to_compat_part(core_part: pb2_v10.Part) -> types_v03.Part:
    """Converts a v1.0 core Part (Protobuf object) to a v0.3 Part (Pydantic model)."""
    which = core_part.WhichOneof('content')
    metadata = (
        MessageToDict(core_part.metadata)
        if core_part.HasField('metadata')
        else None
    )

    if which == 'text':
        return types_v03.Part(
            root=types_v03.TextPart(text=core_part.text, metadata=metadata)
        )

    if which == 'data':
        # core_part.data is a google.protobuf.Value. It can be converted to dict.
        data_dict = MessageToDict(core_part.data)
        if not isinstance(data_dict, dict):
            data_dict = {'value': data_dict}
            metadata = metadata or {}
            metadata['data_part_compat'] = True

        return types_v03.Part(
            root=types_v03.DataPart(data=data_dict, metadata=metadata)
        )

    if which in ('raw', 'url'):
        media_type = core_part.media_type if core_part.media_type else None
        filename = core_part.filename if core_part.filename else None

        if which == 'raw':
            b64 = base64.b64encode(core_part.raw).decode('utf-8')
            file_obj_bytes = types_v03.FileWithBytes(
                bytes=b64, mime_type=media_type, name=filename
            )
            return types_v03.Part(
                root=types_v03.FilePart(file=file_obj_bytes, metadata=metadata)
            )
        file_obj_uri = types_v03.FileWithUri(
            uri=core_part.url, mime_type=media_type, name=filename
        )
        return types_v03.Part(
            root=types_v03.FilePart(file=file_obj_uri, metadata=metadata)
        )

    raise ValueError(f'Unknown part content type: {which}')


def to_core_message(compat_msg: types_v03.Message) -> pb2_v10.Message:
    """Convert message to v1.0 core type."""
    core_msg = pb2_v10.Message(
        message_id=compat_msg.message_id,
        context_id=compat_msg.context_id or '',
        task_id=compat_msg.task_id or '',
    )
    if compat_msg.reference_task_ids:
        core_msg.reference_task_ids.extend(compat_msg.reference_task_ids)

    if compat_msg.role == types_v03.Role.user:
        core_msg.role = pb2_v10.Role.ROLE_USER
    elif compat_msg.role == types_v03.Role.agent:
        core_msg.role = pb2_v10.Role.ROLE_AGENT

    if compat_msg.metadata:
        ParseDict(compat_msg.metadata, core_msg.metadata)

    if compat_msg.extensions:
        core_msg.extensions.extend(compat_msg.extensions)

    for p in compat_msg.parts:
        core_msg.parts.append(to_core_part(p))
    return core_msg


def to_compat_message(core_msg: pb2_v10.Message) -> types_v03.Message:
    """Convert message to v0.3 compat type."""
    role = (
        types_v03.Role.user
        if core_msg.role == pb2_v10.Role.ROLE_USER
        else types_v03.Role.agent
    )
    return types_v03.Message(
        message_id=core_msg.message_id,
        role=role,
        context_id=core_msg.context_id or None,
        task_id=core_msg.task_id or None,
        reference_task_ids=list(core_msg.reference_task_ids)
        if core_msg.reference_task_ids
        else None,
        metadata=MessageToDict(core_msg.metadata)
        if core_msg.metadata
        else None,
        extensions=list(core_msg.extensions) if core_msg.extensions else None,
        parts=[to_compat_part(p) for p in core_msg.parts],
    )


def to_core_task_status(
    compat_status: types_v03.TaskStatus,
) -> pb2_v10.TaskStatus:
    """Convert task status to v1.0 core type."""
    core_status = pb2_v10.TaskStatus()
    if compat_status.state:
        core_status.state = _COMPAT_TO_CORE_TASK_STATE.get(
            compat_status.state, pb2_v10.TaskState.TASK_STATE_UNSPECIFIED
        )

    if compat_status.message:
        core_status.message.CopyFrom(to_core_message(compat_status.message))
    if compat_status.timestamp:
        core_status.timestamp.FromJsonString(
            str(compat_status.timestamp).replace('+00:00', 'Z')
        )
    return core_status


def to_compat_task_status(
    core_status: pb2_v10.TaskStatus,
) -> types_v03.TaskStatus:
    """Convert task status to v0.3 compat type."""
    state_enum = _CORE_TO_COMPAT_TASK_STATE.get(
        core_status.state, types_v03.TaskState.unknown
    )

    update = (
        to_compat_message(core_status.message)
        if core_status.HasField('message')
        else None
    )
    ts = (
        core_status.timestamp.ToJsonString()
        if core_status.HasField('timestamp')
        else None
    )

    return types_v03.TaskStatus(state=state_enum, message=update, timestamp=ts)


def to_core_task(compat_task: types_v03.Task) -> pb2_v10.Task:
    """Convert task to v1.0 core type."""
    core_task = pb2_v10.Task(
        id=compat_task.id,
        context_id=compat_task.context_id,
    )
    if compat_task.status:
        core_task.status.CopyFrom(to_core_task_status(compat_task.status))
    if compat_task.history:
        for m in compat_task.history:
            core_task.history.append(to_core_message(m))
    if compat_task.artifacts:
        for a in compat_task.artifacts:
            core_task.artifacts.append(to_core_artifact(a))
    if compat_task.metadata:
        ParseDict(compat_task.metadata, core_task.metadata)
    return core_task


def to_compat_task(core_task: pb2_v10.Task) -> types_v03.Task:
    """Convert task to v0.3 compat type."""
    return types_v03.Task(
        id=core_task.id,
        context_id=core_task.context_id,
        status=to_compat_task_status(core_task.status)
        if core_task.HasField('status')
        else types_v03.TaskStatus(state=types_v03.TaskState.unknown),
        history=[to_compat_message(m) for m in core_task.history]
        if core_task.history
        else None,
        artifacts=[to_compat_artifact(a) for a in core_task.artifacts]
        if core_task.artifacts
        else None,
        metadata=MessageToDict(core_task.metadata)
        if core_task.HasField('metadata')
        else None,
    )


def to_core_authentication_info(
    compat_auth: types_v03.PushNotificationAuthenticationInfo,
) -> pb2_v10.AuthenticationInfo:
    """Convert authentication info to v1.0 core type."""
    core_auth = pb2_v10.AuthenticationInfo()
    if compat_auth.schemes:
        core_auth.scheme = compat_auth.schemes[0]
    if compat_auth.credentials:
        core_auth.credentials = compat_auth.credentials
    return core_auth


def to_compat_authentication_info(
    core_auth: pb2_v10.AuthenticationInfo,
) -> types_v03.PushNotificationAuthenticationInfo:
    """Convert authentication info to v0.3 compat type."""
    return types_v03.PushNotificationAuthenticationInfo(
        schemes=[core_auth.scheme] if core_auth.scheme else [],
        credentials=core_auth.credentials if core_auth.credentials else None,
    )


def to_core_push_notification_config(
    compat_config: types_v03.PushNotificationConfig,
) -> pb2_v10.TaskPushNotificationConfig:
    """Convert push notification config to v1.0 core type."""
    core_config = pb2_v10.TaskPushNotificationConfig(url=compat_config.url)
    if compat_config.id:
        core_config.id = compat_config.id
    if compat_config.token:
        core_config.token = compat_config.token
    if compat_config.authentication:
        core_config.authentication.CopyFrom(
            to_core_authentication_info(compat_config.authentication)
        )
    return core_config


def to_compat_push_notification_config(
    core_config: pb2_v10.TaskPushNotificationConfig,
) -> types_v03.PushNotificationConfig:
    """Convert push notification config to v0.3 compat type."""
    return types_v03.PushNotificationConfig(
        url=core_config.url if core_config.url else '',
        id=core_config.id if core_config.id else None,
        token=core_config.token if core_config.token else None,
        authentication=to_compat_authentication_info(core_config.authentication)
        if core_config.HasField('authentication')
        else None,
    )


def to_core_send_message_configuration(
    compat_config: types_v03.MessageSendConfiguration,
) -> pb2_v10.SendMessageConfiguration:
    """Convert send message configuration to v1.0 core type."""
    core_config = pb2_v10.SendMessageConfiguration()
    # Result will be blocking by default (return_immediately=False)
    if compat_config.accepted_output_modes:
        core_config.accepted_output_modes.extend(
            compat_config.accepted_output_modes
        )
    if compat_config.push_notification_config:
        core_config.task_push_notification_config.CopyFrom(
            to_core_push_notification_config(
                compat_config.push_notification_config
            )
        )
    if compat_config.history_length is not None:
        core_config.history_length = compat_config.history_length
    if compat_config.blocking is not None:
        core_config.return_immediately = not compat_config.blocking
    return core_config


def to_compat_send_message_configuration(
    core_config: pb2_v10.SendMessageConfiguration,
) -> types_v03.MessageSendConfiguration:
    """Convert send message configuration to v0.3 compat type."""
    return types_v03.MessageSendConfiguration(
        accepted_output_modes=list(core_config.accepted_output_modes)
        if core_config.accepted_output_modes
        else None,
        push_notification_config=to_compat_push_notification_config(
            core_config.task_push_notification_config
        )
        if core_config.HasField('task_push_notification_config')
        else None,
        history_length=core_config.history_length
        if core_config.HasField('history_length')
        else None,
        blocking=not core_config.return_immediately,
    )


def to_core_artifact(compat_artifact: types_v03.Artifact) -> pb2_v10.Artifact:
    """Convert artifact to v1.0 core type."""
    core_artifact = pb2_v10.Artifact(artifact_id=compat_artifact.artifact_id)
    if compat_artifact.name:
        core_artifact.name = compat_artifact.name
    if compat_artifact.description:
        core_artifact.description = compat_artifact.description
    for p in compat_artifact.parts:
        core_artifact.parts.append(to_core_part(p))
    if compat_artifact.metadata:
        ParseDict(compat_artifact.metadata, core_artifact.metadata)
    if compat_artifact.extensions:
        core_artifact.extensions.extend(compat_artifact.extensions)
    return core_artifact


def to_compat_artifact(core_artifact: pb2_v10.Artifact) -> types_v03.Artifact:
    """Convert artifact to v0.3 compat type."""
    return types_v03.Artifact(
        artifact_id=core_artifact.artifact_id,
        name=core_artifact.name if core_artifact.name else None,
        description=core_artifact.description
        if core_artifact.description
        else None,
        parts=[to_compat_part(p) for p in core_artifact.parts],
        metadata=MessageToDict(core_artifact.metadata)
        if core_artifact.HasField('metadata')
        else None,
        extensions=list(core_artifact.extensions)
        if core_artifact.extensions
        else None,
    )


def to_core_task_status_update_event(
    compat_event: types_v03.TaskStatusUpdateEvent,
) -> pb2_v10.TaskStatusUpdateEvent:
    """Convert task status update event to v1.0 core type."""
    core_event = pb2_v10.TaskStatusUpdateEvent(
        task_id=compat_event.task_id, context_id=compat_event.context_id
    )
    if compat_event.status:
        core_event.status.CopyFrom(to_core_task_status(compat_event.status))
    if compat_event.metadata:
        ParseDict(compat_event.metadata, core_event.metadata)
    return core_event


def to_compat_task_status_update_event(
    core_event: pb2_v10.TaskStatusUpdateEvent,
) -> types_v03.TaskStatusUpdateEvent:
    """Convert task status update event to v0.3 compat type."""
    status = (
        to_compat_task_status(core_event.status)
        if core_event.HasField('status')
        else types_v03.TaskStatus(state=types_v03.TaskState.unknown)
    )
    final = status.state in (
        types_v03.TaskState.completed,
        types_v03.TaskState.canceled,
        types_v03.TaskState.failed,
        types_v03.TaskState.rejected,
    )
    return types_v03.TaskStatusUpdateEvent(
        task_id=core_event.task_id,
        context_id=core_event.context_id,
        status=status,
        metadata=MessageToDict(core_event.metadata)
        if core_event.HasField('metadata')
        else None,
        final=final,
    )


def to_core_task_artifact_update_event(
    compat_event: types_v03.TaskArtifactUpdateEvent,
) -> pb2_v10.TaskArtifactUpdateEvent:
    """Convert task artifact update event to v1.0 core type."""
    core_event = pb2_v10.TaskArtifactUpdateEvent(
        task_id=compat_event.task_id, context_id=compat_event.context_id
    )
    if compat_event.artifact:
        core_event.artifact.CopyFrom(to_core_artifact(compat_event.artifact))
    if compat_event.append is not None:
        core_event.append = compat_event.append
    if compat_event.last_chunk is not None:
        core_event.last_chunk = compat_event.last_chunk
    if compat_event.metadata:
        ParseDict(compat_event.metadata, core_event.metadata)
    return core_event


def to_core_security_requirement(
    compat_req: dict[str, list[str]],
) -> pb2_v10.SecurityRequirement:
    """Convert security requirement to v1.0 core type."""
    core_req = pb2_v10.SecurityRequirement()
    for scheme_name, scopes in compat_req.items():
        sl = pb2_v10.StringList()
        sl.list.extend(scopes)
        core_req.schemes[scheme_name].CopyFrom(sl)
    return core_req


def to_compat_security_requirement(
    core_req: pb2_v10.SecurityRequirement,
) -> dict[str, list[str]]:
    """Convert security requirement to v0.3 compat type."""
    return {
        scheme_name: list(string_list.list)
        for scheme_name, string_list in core_req.schemes.items()
    }


def to_core_oauth_flows(
    compat_flows: types_v03.OAuthFlows,
) -> pb2_v10.OAuthFlows:
    """Convert oauth flows to v1.0 core type."""
    core_flows = pb2_v10.OAuthFlows()
    if compat_flows.authorization_code:
        f = pb2_v10.AuthorizationCodeOAuthFlow(
            authorization_url=compat_flows.authorization_code.authorization_url,
            token_url=compat_flows.authorization_code.token_url,
            scopes=compat_flows.authorization_code.scopes,
        )
        if compat_flows.authorization_code.refresh_url:
            f.refresh_url = compat_flows.authorization_code.refresh_url
        core_flows.authorization_code.CopyFrom(f)

    if compat_flows.client_credentials:
        f_client = pb2_v10.ClientCredentialsOAuthFlow(
            token_url=compat_flows.client_credentials.token_url,
            scopes=compat_flows.client_credentials.scopes,
        )
        if compat_flows.client_credentials.refresh_url:
            f_client.refresh_url = compat_flows.client_credentials.refresh_url
        core_flows.client_credentials.CopyFrom(f_client)

    if compat_flows.implicit:
        f_impl = pb2_v10.ImplicitOAuthFlow(
            authorization_url=compat_flows.implicit.authorization_url,
            scopes=compat_flows.implicit.scopes,
        )
        if compat_flows.implicit.refresh_url:
            f_impl.refresh_url = compat_flows.implicit.refresh_url
        core_flows.implicit.CopyFrom(f_impl)

    if compat_flows.password:
        f_pass = pb2_v10.PasswordOAuthFlow(
            token_url=compat_flows.password.token_url,
            scopes=compat_flows.password.scopes,
        )
        if compat_flows.password.refresh_url:
            f_pass.refresh_url = compat_flows.password.refresh_url
        core_flows.password.CopyFrom(f_pass)

    return core_flows


def to_compat_oauth_flows(
    core_flows: pb2_v10.OAuthFlows,
) -> types_v03.OAuthFlows:
    """Convert oauth flows to v0.3 compat type."""
    which = core_flows.WhichOneof('flow')
    auth_code, client_cred, implicit, password = None, None, None, None

    if which == 'authorization_code':
        auth_code = types_v03.AuthorizationCodeOAuthFlow(
            authorization_url=core_flows.authorization_code.authorization_url,
            token_url=core_flows.authorization_code.token_url,
            scopes=dict(core_flows.authorization_code.scopes),
            refresh_url=core_flows.authorization_code.refresh_url
            if core_flows.authorization_code.refresh_url
            else None,
        )
    elif which == 'client_credentials':
        client_cred = types_v03.ClientCredentialsOAuthFlow(
            token_url=core_flows.client_credentials.token_url,
            scopes=dict(core_flows.client_credentials.scopes),
            refresh_url=core_flows.client_credentials.refresh_url
            if core_flows.client_credentials.refresh_url
            else None,
        )
    elif which == 'implicit':
        implicit = types_v03.ImplicitOAuthFlow(
            authorization_url=core_flows.implicit.authorization_url,
            scopes=dict(core_flows.implicit.scopes),
            refresh_url=core_flows.implicit.refresh_url
            if core_flows.implicit.refresh_url
            else None,
        )
    elif which == 'password':
        password = types_v03.PasswordOAuthFlow(
            token_url=core_flows.password.token_url,
            scopes=dict(core_flows.password.scopes),
            refresh_url=core_flows.password.refresh_url
            if core_flows.password.refresh_url
            else None,
        )
    # Note: device_code from v1.0 is dropped since v0.3 doesn't support it

    return types_v03.OAuthFlows(
        authorization_code=auth_code,
        client_credentials=client_cred,
        implicit=implicit,
        password=password,
    )


def to_core_security_scheme(
    compat_scheme: types_v03.SecurityScheme,
) -> pb2_v10.SecurityScheme:
    """Convert security scheme to v1.0 core type."""
    core_scheme = pb2_v10.SecurityScheme()
    root = compat_scheme.root

    if isinstance(root, types_v03.APIKeySecurityScheme):
        core_scheme.api_key_security_scheme.location = root.in_.value
        core_scheme.api_key_security_scheme.name = root.name
        if root.description:
            core_scheme.api_key_security_scheme.description = root.description

    elif isinstance(root, types_v03.HTTPAuthSecurityScheme):
        core_scheme.http_auth_security_scheme.scheme = root.scheme
        if root.bearer_format:
            core_scheme.http_auth_security_scheme.bearer_format = (
                root.bearer_format
            )
        if root.description:
            core_scheme.http_auth_security_scheme.description = root.description

    elif isinstance(root, types_v03.OAuth2SecurityScheme):
        core_scheme.oauth2_security_scheme.flows.CopyFrom(
            to_core_oauth_flows(root.flows)
        )
        if root.oauth2_metadata_url:
            core_scheme.oauth2_security_scheme.oauth2_metadata_url = (
                root.oauth2_metadata_url
            )
        if root.description:
            core_scheme.oauth2_security_scheme.description = root.description

    elif isinstance(root, types_v03.OpenIdConnectSecurityScheme):
        core_scheme.open_id_connect_security_scheme.open_id_connect_url = (
            root.open_id_connect_url
        )
        if root.description:
            core_scheme.open_id_connect_security_scheme.description = (
                root.description
            )

    elif isinstance(root, types_v03.MutualTLSSecurityScheme):
        # Mutual TLS has no required fields other than description which is optional
        core_scheme.mtls_security_scheme.SetInParent()
        if root.description:
            core_scheme.mtls_security_scheme.description = root.description

    return core_scheme


def to_compat_security_scheme(
    core_scheme: pb2_v10.SecurityScheme,
) -> types_v03.SecurityScheme:
    """Convert security scheme to v0.3 compat type."""
    which = core_scheme.WhichOneof('scheme')

    if which == 'api_key_security_scheme':
        s_api = core_scheme.api_key_security_scheme
        return types_v03.SecurityScheme(
            root=types_v03.APIKeySecurityScheme(
                in_=types_v03.In(s_api.location),
                name=s_api.name,
                description=s_api.description if s_api.description else None,
            )
        )

    if which == 'http_auth_security_scheme':
        s_http = core_scheme.http_auth_security_scheme
        return types_v03.SecurityScheme(
            root=types_v03.HTTPAuthSecurityScheme(
                scheme=s_http.scheme,
                bearer_format=s_http.bearer_format
                if s_http.bearer_format
                else None,
                description=s_http.description if s_http.description else None,
            )
        )

    if which == 'oauth2_security_scheme':
        s_oauth = core_scheme.oauth2_security_scheme
        return types_v03.SecurityScheme(
            root=types_v03.OAuth2SecurityScheme(
                flows=to_compat_oauth_flows(s_oauth.flows),
                oauth2_metadata_url=s_oauth.oauth2_metadata_url
                if s_oauth.oauth2_metadata_url
                else None,
                description=s_oauth.description
                if s_oauth.description
                else None,
            )
        )

    if which == 'open_id_connect_security_scheme':
        s_oidc = core_scheme.open_id_connect_security_scheme
        return types_v03.SecurityScheme(
            root=types_v03.OpenIdConnectSecurityScheme(
                open_id_connect_url=s_oidc.open_id_connect_url,
                description=s_oidc.description if s_oidc.description else None,
            )
        )

    if which == 'mtls_security_scheme':
        s_mtls = core_scheme.mtls_security_scheme
        return types_v03.SecurityScheme(
            root=types_v03.MutualTLSSecurityScheme(
                description=s_mtls.description if s_mtls.description else None
            )
        )

    raise ValueError(f'Unknown security scheme type: {which}')


def to_core_agent_interface(
    compat_interface: types_v03.AgentInterface,
) -> pb2_v10.AgentInterface:
    """Convert agent interface to v1.0 core type."""
    return pb2_v10.AgentInterface(
        url=compat_interface.url,
        protocol_binding=compat_interface.transport,
        protocol_version=constants.PROTOCOL_VERSION_0_3,  # Defaulting for legacy
    )


def to_compat_agent_interface(
    core_interface: pb2_v10.AgentInterface,
) -> types_v03.AgentInterface:
    """Convert agent interface to v0.3 compat type."""
    return types_v03.AgentInterface(
        url=core_interface.url, transport=core_interface.protocol_binding
    )


def to_core_agent_provider(
    compat_provider: types_v03.AgentProvider,
) -> pb2_v10.AgentProvider:
    """Convert agent provider to v1.0 core type."""
    return pb2_v10.AgentProvider(
        url=compat_provider.url, organization=compat_provider.organization
    )


def to_compat_agent_provider(
    core_provider: pb2_v10.AgentProvider,
) -> types_v03.AgentProvider:
    """Convert agent provider to v0.3 compat type."""
    return types_v03.AgentProvider(
        url=core_provider.url, organization=core_provider.organization
    )


def to_core_agent_extension(
    compat_ext: types_v03.AgentExtension,
) -> pb2_v10.AgentExtension:
    """Convert agent extension to v1.0 core type."""
    core_ext = pb2_v10.AgentExtension()
    if compat_ext.uri:
        core_ext.uri = compat_ext.uri
    if compat_ext.description:
        core_ext.description = compat_ext.description
    if compat_ext.required is not None:
        core_ext.required = compat_ext.required
    if compat_ext.params:
        ParseDict(compat_ext.params, core_ext.params)
    return core_ext


def to_compat_agent_extension(
    core_ext: pb2_v10.AgentExtension,
) -> types_v03.AgentExtension:
    """Convert agent extension to v0.3 compat type."""
    return types_v03.AgentExtension(
        uri=core_ext.uri,
        description=core_ext.description if core_ext.description else None,
        required=core_ext.required,
        params=MessageToDict(core_ext.params)
        if core_ext.HasField('params')
        else None,
    )


def to_core_agent_capabilities(
    compat_cap: types_v03.AgentCapabilities,
) -> pb2_v10.AgentCapabilities:
    """Convert agent capabilities to v1.0 core type."""
    core_cap = pb2_v10.AgentCapabilities()
    if compat_cap.streaming is not None:
        core_cap.streaming = compat_cap.streaming
    if compat_cap.push_notifications is not None:
        core_cap.push_notifications = compat_cap.push_notifications
    if compat_cap.extensions:
        core_cap.extensions.extend(
            [to_core_agent_extension(e) for e in compat_cap.extensions]
        )
    return core_cap


def to_compat_agent_capabilities(
    core_cap: pb2_v10.AgentCapabilities,
) -> types_v03.AgentCapabilities:
    """Convert agent capabilities to v0.3 compat type."""
    return types_v03.AgentCapabilities(
        streaming=core_cap.streaming
        if core_cap.HasField('streaming')
        else None,
        push_notifications=core_cap.push_notifications
        if core_cap.HasField('push_notifications')
        else None,
        extensions=[to_compat_agent_extension(e) for e in core_cap.extensions]
        if core_cap.extensions
        else None,
        state_transition_history=None,  # No longer supported in v1.0
    )


def to_core_agent_skill(
    compat_skill: types_v03.AgentSkill,
) -> pb2_v10.AgentSkill:
    """Convert agent skill to v1.0 core type."""
    core_skill = pb2_v10.AgentSkill(
        id=compat_skill.id,
        name=compat_skill.name,
        description=compat_skill.description,
    )
    if compat_skill.tags:
        core_skill.tags.extend(compat_skill.tags)
    if compat_skill.examples:
        core_skill.examples.extend(compat_skill.examples)
    if compat_skill.input_modes:
        core_skill.input_modes.extend(compat_skill.input_modes)
    if compat_skill.output_modes:
        core_skill.output_modes.extend(compat_skill.output_modes)
    if compat_skill.security:
        core_skill.security_requirements.extend(
            [to_core_security_requirement(r) for r in compat_skill.security]
        )
    return core_skill


def to_compat_agent_skill(
    core_skill: pb2_v10.AgentSkill,
) -> types_v03.AgentSkill:
    """Convert agent skill to v0.3 compat type."""
    return types_v03.AgentSkill(
        id=core_skill.id,
        name=core_skill.name,
        description=core_skill.description,
        tags=list(core_skill.tags) if core_skill.tags else [],
        examples=list(core_skill.examples) if core_skill.examples else None,
        input_modes=list(core_skill.input_modes)
        if core_skill.input_modes
        else None,
        output_modes=list(core_skill.output_modes)
        if core_skill.output_modes
        else None,
        security=[
            to_compat_security_requirement(r)
            for r in core_skill.security_requirements
        ]
        if core_skill.security_requirements
        else None,
    )


def to_core_agent_card_signature(
    compat_sig: types_v03.AgentCardSignature,
) -> pb2_v10.AgentCardSignature:
    """Convert agent card signature to v1.0 core type."""
    core_sig = pb2_v10.AgentCardSignature(
        protected=compat_sig.protected, signature=compat_sig.signature
    )
    if compat_sig.header:
        ParseDict(compat_sig.header, core_sig.header)
    return core_sig


def to_compat_agent_card_signature(
    core_sig: pb2_v10.AgentCardSignature,
) -> types_v03.AgentCardSignature:
    """Convert agent card signature to v0.3 compat type."""
    return types_v03.AgentCardSignature(
        protected=core_sig.protected,
        signature=core_sig.signature,
        header=MessageToDict(core_sig.header)
        if core_sig.HasField('header')
        else None,
    )


def to_core_agent_card(compat_card: types_v03.AgentCard) -> pb2_v10.AgentCard:
    """Convert agent card to v1.0 core type."""
    core_card = pb2_v10.AgentCard(
        name=compat_card.name,
        description=compat_card.description,
        version=compat_card.version,
    )

    # Map primary interface
    primary_interface = pb2_v10.AgentInterface(
        url=compat_card.url,
        protocol_binding=compat_card.preferred_transport or 'JSONRPC',
        protocol_version=compat_card.protocol_version
        or constants.PROTOCOL_VERSION_0_3,
    )
    core_card.supported_interfaces.append(primary_interface)

    if compat_card.additional_interfaces:
        core_card.supported_interfaces.extend(
            [
                to_core_agent_interface(i)
                for i in compat_card.additional_interfaces
            ]
        )

    if compat_card.provider:
        core_card.provider.CopyFrom(
            to_core_agent_provider(compat_card.provider)
        )

    if compat_card.documentation_url:
        core_card.documentation_url = compat_card.documentation_url

    if compat_card.icon_url:
        core_card.icon_url = compat_card.icon_url

    core_cap = to_core_agent_capabilities(compat_card.capabilities)
    if compat_card.supports_authenticated_extended_card is not None:
        core_cap.extended_agent_card = (
            compat_card.supports_authenticated_extended_card
        )
    core_card.capabilities.CopyFrom(core_cap)

    if compat_card.security_schemes:
        for k, v in compat_card.security_schemes.items():
            core_card.security_schemes[k].CopyFrom(to_core_security_scheme(v))

    if compat_card.security:
        core_card.security_requirements.extend(
            [to_core_security_requirement(r) for r in compat_card.security]
        )

    if compat_card.default_input_modes:
        core_card.default_input_modes.extend(compat_card.default_input_modes)

    if compat_card.default_output_modes:
        core_card.default_output_modes.extend(compat_card.default_output_modes)

    if compat_card.skills:
        core_card.skills.extend(
            [to_core_agent_skill(s) for s in compat_card.skills]
        )

    if compat_card.signatures:
        core_card.signatures.extend(
            [to_core_agent_card_signature(s) for s in compat_card.signatures]
        )

    return core_card


def to_compat_agent_card(core_card: pb2_v10.AgentCard) -> types_v03.AgentCard:
    # Map supported interfaces back to legacy layout
    """Convert agent card to v0.3 compat type."""
    compat_interfaces = [
        interface
        for interface in core_card.supported_interfaces
        if (
            (not interface.protocol_version)
            or is_legacy_version(interface.protocol_version)
        )
    ]
    if not compat_interfaces:
        raise errors.VersionNotSupportedError(
            'AgentCard must have at least one interface with compatible protocol version.'
        )

    primary_interface = compat_interfaces[0]
    additional_interfaces = [
        to_compat_agent_interface(i) for i in compat_interfaces[1:]
    ]

    compat_cap = to_compat_agent_capabilities(core_card.capabilities)
    supports_authenticated_extended_card = (
        core_card.capabilities.extended_agent_card
        if core_card.capabilities.HasField('extended_agent_card')
        else None
    )

    return types_v03.AgentCard(
        name=core_card.name,
        description=core_card.description,
        version=core_card.version,
        url=primary_interface.url,
        preferred_transport=primary_interface.protocol_binding,
        protocol_version=primary_interface.protocol_version
        or constants.PROTOCOL_VERSION_0_3,
        additional_interfaces=additional_interfaces or None,
        provider=to_compat_agent_provider(core_card.provider)
        if core_card.HasField('provider')
        else None,
        documentation_url=core_card.documentation_url
        if core_card.HasField('documentation_url')
        else None,
        icon_url=core_card.icon_url if core_card.HasField('icon_url') else None,
        capabilities=compat_cap,
        supports_authenticated_extended_card=supports_authenticated_extended_card,
        security_schemes={
            k: to_compat_security_scheme(v)
            for k, v in core_card.security_schemes.items()
        }
        if core_card.security_schemes
        else None,
        security=[
            to_compat_security_requirement(r)
            for r in core_card.security_requirements
        ]
        if core_card.security_requirements
        else None,
        default_input_modes=list(core_card.default_input_modes)
        if core_card.default_input_modes
        else [],
        default_output_modes=list(core_card.default_output_modes)
        if core_card.default_output_modes
        else [],
        skills=[to_compat_agent_skill(s) for s in core_card.skills]
        if core_card.skills
        else [],
        signatures=[
            to_compat_agent_card_signature(s) for s in core_card.signatures
        ]
        if core_card.signatures
        else None,
    )


def to_compat_task_artifact_update_event(
    core_event: pb2_v10.TaskArtifactUpdateEvent,
) -> types_v03.TaskArtifactUpdateEvent:
    """Convert task artifact update event to v0.3 compat type."""
    return types_v03.TaskArtifactUpdateEvent(
        task_id=core_event.task_id,
        context_id=core_event.context_id,
        artifact=to_compat_artifact(core_event.artifact),
        append=core_event.append,
        last_chunk=core_event.last_chunk,
        metadata=MessageToDict(core_event.metadata)
        if core_event.HasField('metadata')
        else None,
    )


def to_core_task_push_notification_config(
    compat_config: types_v03.TaskPushNotificationConfig,
) -> pb2_v10.TaskPushNotificationConfig:
    """Convert task push notification config to v1.0 core type."""
    core_config = pb2_v10.TaskPushNotificationConfig(
        task_id=compat_config.task_id
    )
    if compat_config.push_notification_config:
        core_config.MergeFrom(
            to_core_push_notification_config(
                compat_config.push_notification_config
            )
        )
    return core_config


def to_compat_task_push_notification_config(
    core_config: pb2_v10.TaskPushNotificationConfig,
) -> types_v03.TaskPushNotificationConfig:
    """Convert task push notification config to v0.3 compat type."""
    return types_v03.TaskPushNotificationConfig(
        task_id=core_config.task_id,
        push_notification_config=to_compat_push_notification_config(
            core_config
        ),
    )


def to_core_send_message_request(
    compat_req: types_v03.SendMessageRequest,
) -> pb2_v10.SendMessageRequest:
    """Convert send message request to v1.0 core type."""
    core_req = pb2_v10.SendMessageRequest()
    if compat_req.params.message:
        core_req.message.CopyFrom(to_core_message(compat_req.params.message))
    if compat_req.params.configuration:
        core_req.configuration.CopyFrom(
            to_core_send_message_configuration(compat_req.params.configuration)
        )
    if compat_req.params.metadata:
        ParseDict(compat_req.params.metadata, core_req.metadata)
    return core_req


def to_compat_send_message_request(
    core_req: pb2_v10.SendMessageRequest, request_id: str | int
) -> types_v03.SendMessageRequest:
    """Convert send message request to v0.3 compat type."""
    return types_v03.SendMessageRequest(
        id=request_id,
        params=types_v03.MessageSendParams(
            message=to_compat_message(core_req.message),
            configuration=to_compat_send_message_configuration(
                core_req.configuration
            )
            if core_req.HasField('configuration')
            else None,
            metadata=MessageToDict(core_req.metadata)
            if core_req.HasField('metadata')
            else None,
        ),
    )


def to_core_get_task_request(
    compat_req: types_v03.GetTaskRequest,
) -> pb2_v10.GetTaskRequest:
    """Convert get task request to v1.0 core type."""
    core_req = pb2_v10.GetTaskRequest()
    core_req.id = compat_req.params.id
    if compat_req.params.history_length is not None:
        core_req.history_length = compat_req.params.history_length
    return core_req


def to_compat_get_task_request(
    core_req: pb2_v10.GetTaskRequest, request_id: str | int
) -> types_v03.GetTaskRequest:
    """Convert get task request to v0.3 compat type."""
    return types_v03.GetTaskRequest(
        id=request_id,
        params=types_v03.TaskQueryParams(
            id=core_req.id,
            history_length=core_req.history_length
            if core_req.HasField('history_length')
            else None,
        ),
    )


def to_core_cancel_task_request(
    compat_req: types_v03.CancelTaskRequest,
) -> pb2_v10.CancelTaskRequest:
    """Convert cancel task request to v1.0 core type."""
    core_req = pb2_v10.CancelTaskRequest(id=compat_req.params.id)
    if compat_req.params.metadata:
        ParseDict(compat_req.params.metadata, core_req.metadata)
    return core_req


def to_compat_cancel_task_request(
    core_req: pb2_v10.CancelTaskRequest, request_id: str | int
) -> types_v03.CancelTaskRequest:
    """Convert cancel task request to v0.3 compat type."""
    return types_v03.CancelTaskRequest(
        id=request_id,
        params=types_v03.TaskIdParams(
            id=core_req.id,
            metadata=MessageToDict(core_req.metadata)
            if core_req.HasField('metadata')
            else None,
        ),
    )


def to_core_get_task_push_notification_config_request(
    compat_req: types_v03.GetTaskPushNotificationConfigRequest,
) -> pb2_v10.GetTaskPushNotificationConfigRequest:
    """Convert get task push notification config request to v1.0 core type."""
    if isinstance(
        compat_req.params, types_v03.GetTaskPushNotificationConfigParams
    ):
        return pb2_v10.GetTaskPushNotificationConfigRequest(
            task_id=compat_req.params.id,
            id=compat_req.params.push_notification_config_id,
        )
    return pb2_v10.GetTaskPushNotificationConfigRequest(
        task_id=compat_req.params.id
    )


def to_compat_get_task_push_notification_config_request(
    core_req: pb2_v10.GetTaskPushNotificationConfigRequest,
    request_id: str | int,
) -> types_v03.GetTaskPushNotificationConfigRequest:
    """Convert get task push notification config request to v0.3 compat type."""
    params: (
        types_v03.GetTaskPushNotificationConfigParams | types_v03.TaskIdParams
    )
    if core_req.id:
        params = types_v03.GetTaskPushNotificationConfigParams(
            id=core_req.task_id, push_notification_config_id=core_req.id
        )
    else:
        params = types_v03.TaskIdParams(id=core_req.task_id)
    return types_v03.GetTaskPushNotificationConfigRequest(
        id=request_id, params=params
    )


def to_core_delete_task_push_notification_config_request(
    compat_req: types_v03.DeleteTaskPushNotificationConfigRequest,
) -> pb2_v10.DeleteTaskPushNotificationConfigRequest:
    """Convert delete task push notification config request to v1.0 core type."""
    return pb2_v10.DeleteTaskPushNotificationConfigRequest(
        task_id=compat_req.params.id,
        id=compat_req.params.push_notification_config_id,
    )


def to_compat_delete_task_push_notification_config_request(
    core_req: pb2_v10.DeleteTaskPushNotificationConfigRequest,
    request_id: str | int,
) -> types_v03.DeleteTaskPushNotificationConfigRequest:
    """Convert delete task push notification config request to v0.3 compat type."""
    return types_v03.DeleteTaskPushNotificationConfigRequest(
        id=request_id,
        params=types_v03.DeleteTaskPushNotificationConfigParams(
            id=core_req.task_id, push_notification_config_id=core_req.id
        ),
    )


def to_core_create_task_push_notification_config_request(
    compat_req: types_v03.SetTaskPushNotificationConfigRequest,
) -> pb2_v10.TaskPushNotificationConfig:
    """Convert create task push notification config request to v1.0 core type."""
    core_req = pb2_v10.TaskPushNotificationConfig(
        task_id=compat_req.params.task_id
    )
    if compat_req.params.push_notification_config:
        core_req.MergeFrom(
            to_core_push_notification_config(
                compat_req.params.push_notification_config
            )
        )
    return core_req


def to_compat_create_task_push_notification_config_request(
    core_req: pb2_v10.TaskPushNotificationConfig,
    request_id: str | int,
) -> types_v03.SetTaskPushNotificationConfigRequest:
    """Convert create task push notification config request to v0.3 compat type."""
    return types_v03.SetTaskPushNotificationConfigRequest(
        id=request_id,
        params=types_v03.TaskPushNotificationConfig(
            task_id=core_req.task_id,
            push_notification_config=to_compat_push_notification_config(
                core_req
            ),
        ),
    )


def to_core_subscribe_to_task_request(
    compat_req: types_v03.TaskResubscriptionRequest,
) -> pb2_v10.SubscribeToTaskRequest:
    """Convert subscribe to task request to v1.0 core type."""
    return pb2_v10.SubscribeToTaskRequest(id=compat_req.params.id)


def to_compat_subscribe_to_task_request(
    core_req: pb2_v10.SubscribeToTaskRequest, request_id: str | int
) -> types_v03.TaskResubscriptionRequest:
    """Convert subscribe to task request to v0.3 compat type."""
    return types_v03.TaskResubscriptionRequest(
        id=request_id, params=types_v03.TaskIdParams(id=core_req.id)
    )


def to_core_list_task_push_notification_config_request(
    compat_req: types_v03.ListTaskPushNotificationConfigRequest,
) -> pb2_v10.ListTaskPushNotificationConfigsRequest:
    """Convert list task push notification config request to v1.0 core type."""
    core_req = pb2_v10.ListTaskPushNotificationConfigsRequest()
    if compat_req.params.id:
        core_req.task_id = compat_req.params.id
    return core_req


def to_compat_list_task_push_notification_config_request(
    core_req: pb2_v10.ListTaskPushNotificationConfigsRequest,
    request_id: str | int,
) -> types_v03.ListTaskPushNotificationConfigRequest:
    """Convert list task push notification config request to v0.3 compat type."""
    return types_v03.ListTaskPushNotificationConfigRequest(
        id=request_id,
        params=types_v03.ListTaskPushNotificationConfigParams(
            id=core_req.task_id
        ),
    )


def to_core_list_task_push_notification_config_response(
    compat_res: types_v03.ListTaskPushNotificationConfigResponse,
) -> pb2_v10.ListTaskPushNotificationConfigsResponse:
    """Convert list task push notification config response to v1.0 core type."""
    core_res = pb2_v10.ListTaskPushNotificationConfigsResponse()
    root = compat_res.root
    if isinstance(
        root, types_v03.ListTaskPushNotificationConfigSuccessResponse
    ):
        for c in root.result:
            core_res.configs.append(to_core_task_push_notification_config(c))
    return core_res


def to_compat_list_task_push_notification_config_response(
    core_res: pb2_v10.ListTaskPushNotificationConfigsResponse,
    request_id: str | int | None = None,
) -> types_v03.ListTaskPushNotificationConfigResponse:
    """Convert list task push notification config response to v0.3 compat type."""
    return types_v03.ListTaskPushNotificationConfigResponse(
        root=types_v03.ListTaskPushNotificationConfigSuccessResponse(
            id=request_id,
            result=[
                to_compat_task_push_notification_config(c)
                for c in core_res.configs
            ],
        )
    )


def to_core_send_message_response(
    compat_res: types_v03.SendMessageResponse,
) -> pb2_v10.SendMessageResponse:
    """Convert send message response to v1.0 core type."""
    core_res = pb2_v10.SendMessageResponse()
    root = compat_res.root
    if isinstance(root, types_v03.SendMessageSuccessResponse):
        if isinstance(root.result, types_v03.Task):
            core_res.task.CopyFrom(to_core_task(root.result))
        else:
            core_res.message.CopyFrom(to_core_message(root.result))
    return core_res


def to_compat_send_message_response(
    core_res: pb2_v10.SendMessageResponse, request_id: str | int | None = None
) -> types_v03.SendMessageResponse:
    """Convert send message response to v0.3 compat type."""
    if core_res.HasField('task'):
        result_task = to_compat_task(core_res.task)
        return types_v03.SendMessageResponse(
            root=types_v03.SendMessageSuccessResponse(
                id=request_id, result=result_task
            )
        )
    result_msg = to_compat_message(core_res.message)
    return types_v03.SendMessageResponse(
        root=types_v03.SendMessageSuccessResponse(
            id=request_id, result=result_msg
        )
    )


def to_core_stream_response(
    compat_res: types_v03.SendStreamingMessageSuccessResponse,
) -> pb2_v10.StreamResponse:
    """Convert stream response to v1.0 core type."""
    core_res = pb2_v10.StreamResponse()
    root = compat_res.result

    if isinstance(root, types_v03.Message):
        core_res.message.CopyFrom(to_core_message(root))
    elif isinstance(root, types_v03.Task):
        core_res.task.CopyFrom(to_core_task(root))
    elif isinstance(root, types_v03.TaskStatusUpdateEvent):
        core_res.status_update.CopyFrom(to_core_task_status_update_event(root))
    elif isinstance(root, types_v03.TaskArtifactUpdateEvent):
        core_res.artifact_update.CopyFrom(
            to_core_task_artifact_update_event(root)
        )

    return core_res


def to_compat_stream_response(
    core_res: pb2_v10.StreamResponse, request_id: str | int | None = None
) -> types_v03.SendStreamingMessageSuccessResponse:
    """Convert stream response to v0.3 compat type."""
    which = core_res.WhichOneof('payload')
    if which == 'message':
        return types_v03.SendStreamingMessageSuccessResponse(
            id=request_id, result=to_compat_message(core_res.message)
        )
    if which == 'task':
        return types_v03.SendStreamingMessageSuccessResponse(
            id=request_id, result=to_compat_task(core_res.task)
        )
    if which == 'status_update':
        return types_v03.SendStreamingMessageSuccessResponse(
            id=request_id,
            result=to_compat_task_status_update_event(core_res.status_update),
        )
    if which == 'artifact_update':
        return types_v03.SendStreamingMessageSuccessResponse(
            id=request_id,
            result=to_compat_task_artifact_update_event(
                core_res.artifact_update
            ),
        )

    raise ValueError(f'Unknown stream response event type: {which}')


def to_core_get_extended_agent_card_request(
    compat_req: types_v03.GetAuthenticatedExtendedCardRequest,
) -> pb2_v10.GetExtendedAgentCardRequest:
    """Convert get extended agent card request to v1.0 core type."""
    return pb2_v10.GetExtendedAgentCardRequest()


def to_compat_get_extended_agent_card_request(
    core_req: pb2_v10.GetExtendedAgentCardRequest, request_id: str | int
) -> types_v03.GetAuthenticatedExtendedCardRequest:
    """Convert get extended agent card request to v0.3 compat type."""
    return types_v03.GetAuthenticatedExtendedCardRequest(id=request_id)
