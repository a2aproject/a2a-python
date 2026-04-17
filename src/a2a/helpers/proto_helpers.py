"""Unified helper functions for creating and handling A2A types."""

import uuid

from collections.abc import Sequence

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
    role: Role = Role.ROLE_AGENT,
    context_id: str | None = None,
    task_id: str | None = None,
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
    context_id: str | None = None,
    task_id: str | None = None,
    role: Role = Role.ROLE_AGENT,
) -> Message:
    """Creates a new message containing a single text Part."""
    return new_message(
        parts=[Part(text=text)],
        role=role,
        task_id=task_id,
        context_id=context_id,
    )


def get_message_text(message: Message, delimiter: str = '\n') -> str:
    """Extracts and joins all text content from a Message's parts."""
    return delimiter.join(get_text_parts(message.parts))


# --- Artifact Helpers ---


def new_artifact(
    parts: list[Part],
    name: str,
    description: str | None = None,
) -> Artifact:
    """Creates a new Artifact object."""
    return Artifact(
        artifact_id=str(uuid.uuid4()),
        parts=parts,
        name=name,
        description=description,
    )


def new_text_artifact(
    name: str,
    text: str,
    description: str | None = None,
) -> Artifact:
    """Creates a new Artifact object containing only a single text Part."""
    return new_artifact(
        [Part(text=text)],
        name,
        description,
    )


def get_artifact_text(artifact: Artifact, delimiter: str = '\n') -> str:
    """Extracts and joins all text content from an Artifact's parts."""
    return delimiter.join(get_text_parts(artifact.parts))


# --- Task Helpers ---


def new_task_from_user_message(user_message: Message) -> Task:
    """Creates a new Task object from an initial user message."""
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
) -> TaskArtifactUpdateEvent:
    """Creates a TaskArtifactUpdateEvent with a single text artifact."""
    return TaskArtifactUpdateEvent(
        task_id=task_id,
        context_id=context_id,
        artifact=Artifact(
            artifact_id=str(uuid.uuid4()), name=name, parts=[Part(text=text)]
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
