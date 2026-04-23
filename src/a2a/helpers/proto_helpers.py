"""Unified helper functions for creating and handling A2A types."""

import uuid

from collections.abc import Sequence
from typing import Any

from google.protobuf import struct_pb2
from google.protobuf.json_format import ParseDict

from a2a.types.a2a_pb2 import (
    Artifact,
    Message,
    Part,
    Role,
    StreamResponse,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)


# --- Message Helpers ---


def new_message(
    parts: list[Part],
    context_id: str | None = None,
    task_id: str | None = None,
    role: Role = Role.ROLE_AGENT,
) -> Message:
    """Creates a new message containing a list of Parts."""
    return Message(
        role=role,
        parts=parts,
        message_id=str(uuid.uuid4()),
        task_id=task_id,
        context_id=context_id,
    )


def new_text_message(
    text: str,
    media_type: str | None = None,
    context_id: str | None = None,
    task_id: str | None = None,
    role: Role = Role.ROLE_AGENT,
) -> Message:
    """Creates a new message containing a single text Part."""
    return new_message(
        parts=[new_text_part(text, media_type=media_type)],
        context_id=context_id,
        task_id=task_id,
        role=role,
    )


def get_message_text(message: Message, delimiter: str = '\n') -> str:
    """Extracts and joins all text content from a Message's parts."""
    return delimiter.join(get_text_parts(message.parts))


def new_data_message(
    data: Any,
    media_type: str | None = None,
    context_id: str | None = None,
    task_id: str | None = None,
    role: Role = Role.ROLE_AGENT,
) -> Message:
    """Creates a new message containing a single data Part.

    Args:
        data: JSON-serializable data to embed (dict, list, str, etc.).
        media_type: Optional MIME type of the part content (e.g., "text/plain", "application/json", "image/png").
        context_id: Optional context ID.
        task_id: Optional task ID.
        role: The role of the message sender (default: ROLE_AGENT).

    Returns:
        A Message with a single data Part.
    """
    return new_message(
        parts=[new_data_part(data, media_type=media_type)],
        context_id=context_id,
        task_id=task_id,
        role=role,
    )


def new_raw_message(  # noqa: PLR0913
    raw: bytes,
    media_type: str | None = None,
    filename: str | None = None,
    context_id: str | None = None,
    task_id: str | None = None,
    role: Role = Role.ROLE_AGENT,
) -> Message:
    """Creates a new message containing a single raw bytes Part.

    Args:
        raw: The raw bytes content.
        media_type: Optional MIME type (e.g. 'image/png').
        filename: Optional filename.
        context_id: Optional context ID.
        task_id: Optional task ID.
        role: The role of the message sender (default: ROLE_AGENT).

    Returns:
        A Message with a single raw Part.
    """
    return new_message(
        parts=[new_raw_part(raw, media_type=media_type, filename=filename)],
        context_id=context_id,
        task_id=task_id,
        role=role,
    )


def new_url_message(  # noqa: PLR0913
    url: str,
    media_type: str | None = None,
    filename: str | None = None,
    context_id: str | None = None,
    task_id: str | None = None,
    role: Role = Role.ROLE_AGENT,
) -> Message:
    """Creates a new message containing a single URL Part.

    Args:
        url: The URL pointing to the file content.
        media_type: Optional MIME type (e.g. 'image/png').
        filename: Optional filename.
        context_id: Optional context ID.
        task_id: Optional task ID.
        role: The role of the message sender (default: ROLE_AGENT).

    Returns:
        A Message with a single URL Part.
    """
    return new_message(
        parts=[new_url_part(url, media_type=media_type, filename=filename)],
        context_id=context_id,
        task_id=task_id,
        role=role,
    )


# --- Artifact Helpers ---


def new_artifact(
    parts: list[Part],
    name: str,
    description: str | None = None,
    artifact_id: str | None = None,
) -> Artifact:
    """Creates a new Artifact object."""
    return Artifact(
        artifact_id=artifact_id or str(uuid.uuid4()),
        parts=parts,
        name=name,
        description=description,
    )


def new_text_artifact(
    name: str,
    text: str,
    media_type: str | None = None,
    description: str | None = None,
    artifact_id: str | None = None,
) -> Artifact:
    """Creates a new Artifact object containing only a single text Part."""
    return new_artifact(
        [new_text_part(text, media_type=media_type)],
        name,
        description,
        artifact_id=artifact_id,
    )


def new_data_artifact(
    name: str,
    data: Any,
    media_type: str | None = None,
    description: str | None = None,
    artifact_id: str | None = None,
) -> Artifact:
    """Creates a new Artifact object containing only a single data Part.

    Args:
        name: The name of the artifact.
        data: JSON-serializable data to embed (dict, list, str, etc.).
        media_type: Optional MIME type of the part content (e.g., "text/plain", "application/json", "image/png").
        description: Optional description.
        artifact_id: Optional artifact ID (auto-generated if not provided).

    Returns:
        An Artifact with a single data Part.
    """
    return new_artifact(
        [new_data_part(data, media_type=media_type)],
        name,
        description,
        artifact_id=artifact_id,
    )


def new_raw_artifact(  # noqa: PLR0913
    name: str,
    raw: bytes,
    media_type: str | None = None,
    filename: str | None = None,
    description: str | None = None,
    artifact_id: str | None = None,
) -> Artifact:
    """Creates a new Artifact object containing only a single raw bytes Part.

    Args:
        name: The name of the artifact.
        raw: The raw bytes content.
        media_type: Optional MIME type (e.g. 'image/png').
        filename: Optional filename.
        description: Optional description.
        artifact_id: Optional artifact ID (auto-generated if not provided).

    Returns:
        An Artifact with a single raw Part.
    """
    return new_artifact(
        [new_raw_part(raw, media_type=media_type, filename=filename)],
        name,
        description,
        artifact_id=artifact_id,
    )


def new_url_artifact(  # noqa: PLR0913
    name: str,
    url: str,
    media_type: str | None = None,
    filename: str | None = None,
    description: str | None = None,
    artifact_id: str | None = None,
) -> Artifact:
    """Creates a new Artifact object containing only a single URL Part.

    Args:
        name: The name of the artifact.
        url: The URL pointing to the file content.
        media_type: Optional MIME type (e.g. 'image/png').
        filename: Optional filename.
        description: Optional description.
        artifact_id: Optional artifact ID (auto-generated if not provided).

    Returns:
        An Artifact with a single URL Part.
    """
    return new_artifact(
        [new_url_part(url, media_type=media_type, filename=filename)],
        name,
        description,
        artifact_id=artifact_id,
    )


def get_artifact_text(artifact: Artifact, delimiter: str = '\n') -> str:
    """Extracts and joins all text content from an Artifact's parts."""
    return delimiter.join(get_text_parts(artifact.parts))


# --- Task Helpers ---


def new_task_from_user_message(user_message: Message) -> Task:
    """Creates a new Task object from an initial user message."""
    if user_message.role != Role.ROLE_USER:
        raise ValueError('Message must be from a user')
    if not user_message.parts:
        raise ValueError('Message parts cannot be empty')
    for part in user_message.parts:
        if part.HasField('text') and not part.text:
            raise ValueError('Message.text cannot be empty')

    return Task(
        status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
        id=user_message.task_id or str(uuid.uuid4()),
        context_id=user_message.context_id or str(uuid.uuid4()),
        history=[user_message],
    )


def new_task(
    task_id: str,
    context_id: str,
    state: TaskState,
    artifacts: list[Artifact] | None = None,
    history: list[Message] | None = None,
) -> Task:
    """Creates a Task object with a specified status."""
    if history is None:
        history = []
    if artifacts is None:
        artifacts = []

    return Task(
        status=TaskStatus(state=state),
        id=task_id,
        context_id=context_id,
        artifacts=artifacts,
        history=history,
    )


# --- Part Helpers ---


def new_text_part(
    text: str,
    media_type: str | None = None,
) -> Part:
    """Creates a Part with text content.

    Args:
        text: The text content.
        media_type: Optional MIME type (e.g. 'text/plain', 'text/markdown').

    Returns:
        A Part with the text field set.
    """
    return Part(text=text, media_type=media_type or '')


def new_data_part(
    data: Any,
    media_type: str | None = None,
) -> Part:
    """Creates a Part with structured data (google.protobuf.Value).

    Args:
        data: JSON-serializable data to embed (dict, list, str, etc.).
        media_type: Optional MIME type of the part content (e.g., "text/plain", "application/json", "image/png").

    Returns:
        A Part with the data field set.
    """
    return Part(
        data=ParseDict(data, struct_pb2.Value()),
        media_type=media_type or '',
    )


def new_raw_part(
    raw: bytes,
    media_type: str | None = None,
    filename: str | None = None,
) -> Part:
    """Creates a Part with raw bytes content.

    Args:
        raw: The raw bytes content.
        media_type: Optional MIME type (e.g. 'image/png').
        filename: Optional filename.

    Returns:
        A Part with the raw field set.
    """
    return Part(
        raw=raw,
        media_type=media_type or '',
        filename=filename or '',
    )


def new_url_part(
    url: str,
    media_type: str | None = None,
    filename: str | None = None,
) -> Part:
    """Creates a Part with a URL pointing to file content.

    Args:
        url: The URL to the file content.
        media_type: Optional MIME type (e.g. 'image/png').
        filename: Optional filename.

    Returns:
        A Part with the url field set.
    """
    return Part(
        url=url,
        media_type=media_type or '',
        filename=filename or '',
    )


def get_text_parts(parts: Sequence[Part]) -> list[str]:
    """Extracts text content from all text Parts."""
    return [part.text for part in parts if part.HasField('text')]


# --- Event & Stream Helpers ---


def new_text_status_update_event(
    task_id: str,
    context_id: str,
    state: TaskState,
    text: str,
) -> TaskStatusUpdateEvent:
    """Creates a TaskStatusUpdateEvent with a single text message."""
    return TaskStatusUpdateEvent(
        task_id=task_id,
        context_id=context_id,
        status=TaskStatus(
            state=state,
            message=new_text_message(
                text=text,
                role=Role.ROLE_AGENT,
                context_id=context_id,
                task_id=task_id,
            ),
        ),
    )


def new_text_artifact_update_event(  # noqa: PLR0913
    task_id: str,
    context_id: str,
    name: str,
    text: str,
    append: bool = False,
    last_chunk: bool = False,
    artifact_id: str | None = None,
) -> TaskArtifactUpdateEvent:
    """Creates a TaskArtifactUpdateEvent with a single text artifact."""
    return TaskArtifactUpdateEvent(
        task_id=task_id,
        context_id=context_id,
        artifact=new_text_artifact(
            name=name, text=text, artifact_id=artifact_id
        ),
        append=append,
        last_chunk=last_chunk,
    )


def get_stream_response_text(
    response: StreamResponse, delimiter: str = '\n'
) -> str:
    """Extracts text content from a StreamResponse."""
    if response.HasField('message'):
        return get_message_text(response.message, delimiter)
    if response.HasField('task'):
        texts = [
            get_artifact_text(a, delimiter) for a in response.task.artifacts
        ]
        return delimiter.join(t for t in texts if t)
    if response.HasField('status_update'):
        if response.status_update.status.HasField('message'):
            return get_message_text(
                response.status_update.status.message, delimiter
            )
        return ''
    if response.HasField('artifact_update'):
        return get_artifact_text(response.artifact_update.artifact, delimiter)
    return ''
