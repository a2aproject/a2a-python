try:
    from google.genai import types as genai_types
    from vertexai import types as vertexai_types
except ImportError as e:
    raise ImportError(
        'vertex_task_converter requires vertexai. '
        'Install with: '
        "'pip install a2a-sdk[vertex]'"
    ) from e

import base64
import json

from dataclasses import dataclass
from typing import Any

from a2a.compat.v0_3.types import (
    Artifact,
    DataPart,
    FilePart,
    FileWithBytes,
    FileWithUri,
    Message,
    Part,
    Role,
    Task,
    TaskState,
    TaskStatus,
    TextPart,
)


_ORIGINAL_METADATA_KEY = 'originalMetadata'
_EXTENSIONS_KEY = 'extensions'
_REFERENCE_TASK_IDS_KEY = 'referenceTaskIds'
_PART_METADATA_KEY = 'partMetadata'
_METADATA_VERSION_KEY = '__vertex_compat_v'
_METADATA_VERSION_NUMBER = 1.0

_DATA_PART_MIME_TYPE = 'application/x-a2a-datapart'


_TO_SDK_TASK_STATE = {
    vertexai_types.A2aTaskState.STATE_UNSPECIFIED: TaskState.unknown,
    vertexai_types.A2aTaskState.SUBMITTED: TaskState.submitted,
    vertexai_types.A2aTaskState.WORKING: TaskState.working,
    vertexai_types.A2aTaskState.COMPLETED: TaskState.completed,
    vertexai_types.A2aTaskState.CANCELLED: TaskState.canceled,
    vertexai_types.A2aTaskState.FAILED: TaskState.failed,
    vertexai_types.A2aTaskState.REJECTED: TaskState.rejected,
    vertexai_types.A2aTaskState.INPUT_REQUIRED: TaskState.input_required,
    vertexai_types.A2aTaskState.AUTH_REQUIRED: TaskState.auth_required,
}

_SDK_TO_STORED_TASK_STATE = {v: k for k, v in _TO_SDK_TASK_STATE.items()}


def to_sdk_task_state(stored_state: vertexai_types.A2aTaskState) -> TaskState:
    """Converts a proto A2aTask.State to a TaskState enum."""
    return _TO_SDK_TASK_STATE.get(stored_state, TaskState.unknown)


def to_stored_task_state(task_state: TaskState) -> vertexai_types.A2aTaskState:
    """Converts a TaskState enum to a proto A2aTask.State enum value."""
    return _SDK_TO_STORED_TASK_STATE.get(
        task_state, vertexai_types.A2aTaskState.STATE_UNSPECIFIED
    )


def to_stored_metadata(
    original_metadata: dict[str, Any] | None,
    extensions: list[str] | None,
    reference_task_ids: list[str] | None,
    parts: list[Part],
) -> dict[str, Any]:
    """Packs original metadata, extensions, and part types/metadata into a storage dictionary."""
    metadata: dict[str, Any] = {_METADATA_VERSION_KEY: _METADATA_VERSION_NUMBER}
    if original_metadata:
        metadata[_ORIGINAL_METADATA_KEY] = original_metadata
    if extensions:
        metadata[_EXTENSIONS_KEY] = extensions
    if reference_task_ids:
        metadata[_REFERENCE_TASK_IDS_KEY] = reference_task_ids

    metadata[_PART_METADATA_KEY] = [part.root.metadata for part in parts]

    return metadata


@dataclass
class _UnpackedMetadata:
    original_metadata: dict[str, Any] | None = None
    extensions: list[str] | None = None
    reference_task_ids: list[str] | None = None
    part_metadata: list[dict[str, Any] | None] | None = None


def to_sdk_metadata(
    stored_metadata: dict[str, Any] | None,
) -> _UnpackedMetadata:
    """Unpacks metadata, extensions, and part types/metadata from a storage dictionary."""
    if not stored_metadata:
        return _UnpackedMetadata()

    version = stored_metadata.get(_METADATA_VERSION_KEY)
    if version is None:
        return _UnpackedMetadata(original_metadata=stored_metadata)
    if version > _METADATA_VERSION_NUMBER:
        raise ValueError(f'Unsupported metadata version: {version}')

    return _UnpackedMetadata(
        original_metadata=stored_metadata.get(_ORIGINAL_METADATA_KEY),
        extensions=stored_metadata.get(_EXTENSIONS_KEY),
        reference_task_ids=stored_metadata.get(_REFERENCE_TASK_IDS_KEY),
        part_metadata=stored_metadata.get(_PART_METADATA_KEY),
    )


def to_stored_part(part: Part) -> genai_types.Part:
    """Converts a SDK Part to a proto Part."""
    if isinstance(part.root, TextPart):
        return genai_types.Part(text=part.root.text)
    if isinstance(part.root, DataPart):
        data_bytes = json.dumps(part.root.data).encode('utf-8')
        return genai_types.Part(
            inline_data=genai_types.Blob(
                mime_type=_DATA_PART_MIME_TYPE, data=data_bytes
            )
        )
    if isinstance(part.root, FilePart):
        file_content = part.root.file
        if isinstance(file_content, FileWithBytes):
            decoded_bytes = base64.b64decode(file_content.bytes)
            return genai_types.Part(
                inline_data=genai_types.Blob(
                    mime_type=file_content.mime_type or '', data=decoded_bytes
                )
            )
        if isinstance(file_content, FileWithUri):
            return genai_types.Part(
                file_data=genai_types.FileData(
                    mime_type=file_content.mime_type or '',
                    file_uri=file_content.uri,
                )
            )
    raise ValueError(f'Unsupported part type: {type(part.root)}')


def to_sdk_part(
    stored_part: genai_types.Part,
    part_metadata: dict[str, Any] | None = None,
) -> Part:
    """Converts a proto Part to a SDK Part."""
    if stored_part.text:
        return Part(
            root=TextPart(text=stored_part.text, metadata=part_metadata)
        )
    if stored_part.inline_data:
        mime_type = stored_part.inline_data.mime_type
        if mime_type == _DATA_PART_MIME_TYPE:
            data_dict = json.loads(stored_part.inline_data.data or b'{}')
            return Part(root=DataPart(data=data_dict, metadata=part_metadata))

        encoded_bytes = base64.b64encode(
            stored_part.inline_data.data or b''
        ).decode('utf-8')
        return Part(
            root=FilePart(
                file=FileWithBytes(
                    mime_type=mime_type,
                    bytes=encoded_bytes,
                ),
                metadata=part_metadata,
            )
        )
    if stored_part.file_data and stored_part.file_data.file_uri:
        return Part(
            root=FilePart(
                file=FileWithUri(
                    mime_type=stored_part.file_data.mime_type,
                    uri=stored_part.file_data.file_uri or '',
                ),
                metadata=part_metadata,
            )
        )

    raise ValueError(f'Unsupported part: {stored_part}')


def to_stored_artifact(artifact: Artifact) -> vertexai_types.TaskArtifact:
    """Converts a SDK Artifact to a proto TaskArtifact."""
    return vertexai_types.TaskArtifact(
        artifact_id=artifact.artifact_id,
        display_name=artifact.name,
        description=artifact.description,
        parts=[to_stored_part(part) for part in artifact.parts],
        metadata=to_stored_metadata(
            original_metadata=artifact.metadata,
            extensions=artifact.extensions,
            reference_task_ids=None,
            parts=artifact.parts,
        ),
    )


def to_sdk_artifact(stored_artifact: vertexai_types.TaskArtifact) -> Artifact:
    """Converts a proto TaskArtifact to a SDK Artifact."""
    unpacked_meta = to_sdk_metadata(stored_artifact.metadata)
    part_metadata_list = unpacked_meta.part_metadata or []

    parts = []
    for i, part in enumerate(stored_artifact.parts or []):
        meta: dict[str, Any] | None = None
        if i < len(part_metadata_list):
            meta = part_metadata_list[i]
        parts.append(to_sdk_part(part, part_metadata=meta))

    return Artifact(
        artifact_id=stored_artifact.artifact_id,
        name=stored_artifact.display_name,
        description=stored_artifact.description,
        extensions=unpacked_meta.extensions,
        metadata=unpacked_meta.original_metadata,
        parts=parts,
    )


def to_stored_message(
    message: Message | None,
) -> vertexai_types.TaskMessage | None:
    """Converts a SDK Message to a proto Message."""
    if not message:
        return None
    role = message.role.value if message.role else ''
    return vertexai_types.TaskMessage(
        message_id=message.message_id,
        role=role,
        parts=[to_stored_part(part) for part in message.parts],
        metadata=to_stored_metadata(
            original_metadata=message.metadata,
            extensions=message.extensions,
            reference_task_ids=message.reference_task_ids,
            parts=message.parts,
        ),
    )


def to_sdk_message(
    stored_msg: vertexai_types.TaskMessage | None,
) -> Message | None:
    """Converts a proto Message to a SDK Message."""
    if not stored_msg:
        return None
    unpacked_meta = to_sdk_metadata(stored_msg.metadata)
    part_metadata_list = unpacked_meta.part_metadata or []

    parts = []
    for i, part in enumerate(stored_msg.parts or []):
        part_metadata: dict[str, Any] | None = None
        if i < len(part_metadata_list):
            part_metadata = part_metadata_list[i]
        parts.append(to_sdk_part(part, part_metadata=part_metadata))

    return Message(
        message_id=stored_msg.message_id,
        role=Role(stored_msg.role),
        extensions=unpacked_meta.extensions,
        reference_task_ids=unpacked_meta.reference_task_ids,
        metadata=unpacked_meta.original_metadata,
        parts=parts,
    )


def to_stored_task(task: Task) -> vertexai_types.A2aTask:
    """Converts a SDK Task to a proto A2aTask."""
    return vertexai_types.A2aTask(
        context_id=task.context_id,
        metadata=task.metadata,
        state=to_stored_task_state(task.status.state),
        status_details=vertexai_types.TaskStatusDetails(
            task_message=to_stored_message(task.status.message)
        )
        if task.status.message
        else None,
        output=vertexai_types.TaskOutput(
            artifacts=[
                to_stored_artifact(artifact)
                for artifact in task.artifacts or []
            ]
        ),
    )


def to_sdk_task(a2a_task: vertexai_types.A2aTask) -> Task:
    """Converts a proto A2aTask to a SDK Task."""
    msg: Message | None = None
    if a2a_task.status_details and a2a_task.status_details.task_message:
        msg = to_sdk_message(a2a_task.status_details.task_message)

    return Task(
        id=a2a_task.name.split('/')[-1],
        context_id=a2a_task.context_id,
        status=TaskStatus(state=to_sdk_task_state(a2a_task.state), message=msg),
        metadata=a2a_task.metadata or {},
        artifacts=[
            to_sdk_artifact(artifact)
            for artifact in a2a_task.output.artifacts or []
        ]
        if a2a_task.output
        else [],
        history=[],
    )
