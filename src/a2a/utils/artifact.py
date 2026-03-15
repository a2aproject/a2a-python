"""Utility functions for creating A2A Artifact objects."""

import uuid

from typing import Any

from google.protobuf.struct_pb2 import Struct, Value

from a2a.types.a2a_pb2 import Artifact, Part, TaskArtifactUpdateEvent
from a2a.utils.parts import get_text_parts


def new_artifact(
    parts: list[Part],
    name: str,
    description: str | None = None,
) -> Artifact:
    """Creates a new Artifact object.

    Args:
        parts: The list of `Part` objects forming the artifact's content.
        name: The human-readable name of the artifact.
        description: An optional description of the artifact.

    Returns:
        A new `Artifact` object with a generated artifact_id.
    """
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
    """Creates a new Artifact object containing only a single text Part.

    Args:
        name: The human-readable name of the artifact.
        text: The text content of the artifact.
        description: An optional description of the artifact.

    Returns:
        A new `Artifact` object with a generated artifact_id.
    """
    return new_artifact(
        [Part(text=text)],
        name,
        description,
    )


def new_data_artifact(
    name: str,
    data: dict[str, Any],
    description: str | None = None,
) -> Artifact:
    """Creates a new Artifact object containing only a single data Part.

    Args:
        name: The human-readable name of the artifact.
        data: The structured data content of the artifact.
        description: An optional description of the artifact.

    Returns:
        A new `Artifact` object with a generated artifact_id.
    """
    struct_data = Struct()
    struct_data.update(data)
    return new_artifact(
        [Part(data=Value(struct_value=struct_data))],
        name,
        description,
    )


def get_artifact_text(artifact: Artifact, delimiter: str = '\n') -> str:
    """Extracts and joins all text content from an Artifact's parts.

    Args:
        artifact: The `Artifact` object.
        delimiter: The string to use when joining text from multiple TextParts.

    Returns:
        A single string containing all text content, or an empty string if no text parts are found.
    """
    return delimiter.join(get_text_parts(artifact.parts))


class ArtifactStreamer:
    """Helper for streaming text into a single artifact across multiple events.

    Creates a stable artifact ID on construction so all chunks reference
    the same artifact, enabling proper append semantics per the A2A spec.

    Example::

        streamer = ArtifactStreamer(context_id, task_id, name='response')

        async for chunk in llm.stream(prompt):
            await event_queue.enqueue_event(streamer.append(chunk))

        await event_queue.enqueue_event(streamer.finalize())

    Args:
        context_id: The context ID associated with the task.
        task_id: The task ID associated with the streaming session.
        name: A human-readable name for the artifact.
        artifact_id: An explicit artifact ID. If omitted a UUID is generated.
    """

    def __init__(
        self,
        context_id: str,
        task_id: str,
        name: str = 'response',
        artifact_id: str | None = None,
    ) -> None:
        self._context_id = context_id
        self._task_id = task_id
        self._name = name
        self._artifact_id = artifact_id or str(uuid.uuid4())

    def append(self, text: str) -> TaskArtifactUpdateEvent:
        """Emit a chunk to be appended to the streaming artifact.

        Args:
            text: The incremental text content for this chunk.

        Returns:
            A ``TaskArtifactUpdateEvent`` with ``append=True`` and
            ``last_chunk=False``.
        """
        return TaskArtifactUpdateEvent(
            context_id=self._context_id,
            task_id=self._task_id,
            append=True,
            last_chunk=False,
            artifact=Artifact(
                artifact_id=self._artifact_id,
                name=self._name,
                parts=[Part(text=text)],
            )
        )

    def finalize(self) -> TaskArtifactUpdateEvent:
        """Signal that the artifact stream is complete.

        Returns:
            A ``TaskArtifactUpdateEvent`` with ``append=True`` and
            ``last_chunk=True``.
        """
        return TaskArtifactUpdateEvent(
            context_id=self._context_id,
            task_id=self._task_id,
            append=True,
            last_chunk=True,
            artifact=Artifact(
                artifact_id=self._artifact_id,
                name=self._name,
                parts=[],
            )
        )
