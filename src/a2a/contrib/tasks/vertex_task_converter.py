try:
    from vertexai import types as vertexai_types
except ImportError as e:
    raise ImportError(
        'vertex_task_converter requires vertexai. '
        'Install with: '
        "'pip install a2a-sdk[vertex]'"
    ) from e

import base64
import json

from a2a.types import (
    Artifact,
    DataPart,
    FilePart,
    FileWithBytes,
    FileWithUri,
    Part,
    Task,
    TaskState,
    TaskStatus,
    TextPart,
)


_TO_SDK_TASK_STATE = {
    vertexai_types.State.STATE_UNSPECIFIED: TaskState.unknown,
    vertexai_types.State.SUBMITTED: TaskState.submitted,
    vertexai_types.State.WORKING: TaskState.working,
    vertexai_types.State.COMPLETED: TaskState.completed,
    vertexai_types.State.CANCELLED: TaskState.canceled,
    vertexai_types.State.FAILED: TaskState.failed,
    vertexai_types.State.REJECTED: TaskState.rejected,
    vertexai_types.State.INPUT_REQUIRED: TaskState.input_required,
    vertexai_types.State.AUTH_REQUIRED: TaskState.auth_required,
}

_SDK_TO_STORED_TASK_STATE = {v: k for k, v in _TO_SDK_TASK_STATE.items()}


def to_sdk_task_state(stored_state: vertexai_types.State) -> TaskState:
    """Converts a proto A2aTask.State to a TaskState enum."""
    return _TO_SDK_TASK_STATE.get(stored_state, TaskState.unknown)


def to_stored_task_state(task_state: TaskState) -> vertexai_types.State:
    """Converts a TaskState enum to a proto A2aTask.State enum value."""
    return _SDK_TO_STORED_TASK_STATE.get(
        task_state, vertexai_types.State.STATE_UNSPECIFIED
    )


def to_stored_part(part: Part) -> vertexai_types.Part:
    """Converts a SDK Part to a proto Part."""
    if isinstance(part.root, TextPart):
        return vertexai_types.Part(text=part.root.text)
    if isinstance(part.root, DataPart):
        data_bytes = json.dumps(part.root.data).encode('utf-8')
        return vertexai_types.Part(
            inline_data=vertexai_types.Blob(
                mime_type='application/json', data=data_bytes
            )
        )
    if isinstance(part.root, FilePart):
        file_content = part.root.file
        if isinstance(file_content, FileWithBytes):
            decoded_bytes = base64.b64decode(file_content.bytes)
            return vertexai_types.Part(
                inline_data=vertexai_types.Blob(
                    mime_type=file_content.mime_type or '', data=decoded_bytes
                )
            )
        if isinstance(file_content, FileWithUri):
            return vertexai_types.Part(
                file_data=vertexai_types.FileData(
                    mime_type=file_content.mime_type or '',
                    file_uri=file_content.uri,
                )
            )
    raise ValueError(f'Unsupported part type: {type(part.root)}')


def to_sdk_part(stored_part: vertexai_types.Part) -> Part:
    """Converts a proto Part to a SDK Part."""
    if stored_part.text:
        return Part(root=TextPart(text=stored_part.text))
    if stored_part.inline_data:
        encoded_bytes = base64.b64encode(stored_part.inline_data.data).decode(
            'utf-8'
        )
        return Part(
            root=FilePart(
                file=FileWithBytes(
                    mime_type=stored_part.inline_data.mime_type,
                    bytes=encoded_bytes,
                )
            )
        )
    if stored_part.file_data:
        return Part(
            root=FilePart(
                file=FileWithUri(
                    mime_type=stored_part.file_data.mime_type,
                    uri=stored_part.file_data.file_uri,
                )
            )
        )

    raise ValueError(f'Unsupported part: {stored_part}')


def to_stored_artifact(artifact: Artifact) -> vertexai_types.TaskArtifact:
    """Converts a SDK Artifact to a proto TaskArtifact."""
    return vertexai_types.TaskArtifact(
        artifact_id=artifact.artifact_id,
        parts=[to_stored_part(part) for part in artifact.parts],
    )


def to_sdk_artifact(stored_artifact: vertexai_types.TaskArtifact) -> Artifact:
    """Converts a proto TaskArtifact to a SDK Artifact."""
    return Artifact(
        artifact_id=stored_artifact.artifact_id,
        parts=[to_sdk_part(part) for part in stored_artifact.parts],
    )


def to_stored_task(task: Task) -> vertexai_types.A2aTask:
    """Converts a SDK Task to a proto A2aTask."""
    return vertexai_types.A2aTask(
        context_id=task.context_id,
        metadata=task.metadata,
        state=to_stored_task_state(task.status.state),
        output=vertexai_types.TaskOutput(
            artifacts=[
                to_stored_artifact(artifact)
                for artifact in task.artifacts or []
            ]
        ),
    )


def to_sdk_task(a2a_task: vertexai_types.A2aTask) -> Task:
    """Converts a proto A2aTask to a SDK Task."""
    return Task(
        id=a2a_task.name.split('/')[-1],
        context_id=a2a_task.context_id,
        status=TaskStatus(state=to_sdk_task_state(a2a_task.state)),
        metadata=a2a_task.metadata or {},
        artifacts=[
            to_sdk_artifact(artifact)
            for artifact in a2a_task.output.artifacts or []
        ]
        if a2a_task.output
        else [],
        history=[],
    )
