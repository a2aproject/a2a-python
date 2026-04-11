"""Utility functions for creating A2A Artifact objects."""

import uuid

from typing import Any

from a2a.types import (
    Artifact,
    DataPart,
    Part,
    TaskArtifactUpdateEvent,
    TextPart,
)
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
    """Creates a new Artifact object containing only a single TextPart.

    Args:
        name: The human-readable name of the artifact.
        text: The text content of the artifact.
        description: An optional description of the artifact.

    Returns:
        A new `Artifact` object with a generated artifact_id.
    """
    return new_artifact(
        [Part(root=TextPart(text=text))],
        name,
        description,
    )


def new_data_artifact(
    name: str,
    data: dict[str, Any],
    description: str | None = None,
) -> Artifact:
    """Creates a new Artifact object containing only a single DataPart.

    Args:
        name: The human-readable name of the artifact.
        data: The structured data content of the artifact.
        description: An optional description of the artifact.

    Returns:
        A new `Artifact` object with a generated artifact_id.
    """
    return new_artifact(
        [Part(root=DataPart(data=data))],
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
    """A stateful helper for streaming artifact updates with a stable artifact ID.

    Solves the problem where calling ``new_text_artifact`` in a loop generates
    a fresh ``artifact_id`` each time, making ``append=True`` unusable.

    Example::

        streamer = ArtifactStreamer(context_id, task_id, name='response')

        async for chunk in llm.stream(prompt):
            await event_queue.enqueue_event(streamer.append(chunk))

        await event_queue.enqueue_event(streamer.finalize())

    Args:
        context_id: The context ID associated with the task.
        task_id: The ID of the task this artifact belongs to.
        name: A human-readable name for the artifact.
        description: An optional description of the artifact.
    """

    def __init__(
        self,
        context_id: str,
        task_id: str,
        name: str,
        description: str | None = None,
    ) -> None:
        self._context_id = context_id
        self._task_id = task_id
        self._name = name
        self._description = description
        self._artifact_id = str(uuid.uuid4())
        self._finalized = False

    @property
    def artifact_id(self) -> str:
        """The stable artifact ID used across all chunks."""
        return self._artifact_id

    def append(self, text: str) -> TaskArtifactUpdateEvent:
        """Create an append event for the next chunk of text.

        Args:
            text: The text content to append.

        Returns:
            A ``TaskArtifactUpdateEvent`` with ``append=True`` and
            ``last_chunk=False``.

        Raises:
            RuntimeError: If ``finalize()`` has already been called.
        """
        if self._finalized:
            raise RuntimeError(
                'Cannot append after finalize() has been called.'
            )
        return TaskArtifactUpdateEvent(
            context_id=self._context_id,
            task_id=self._task_id,
            append=True,
            last_chunk=False,
            artifact=Artifact(
                artifact_id=self._artifact_id,
                name=self._name,
                description=self._description,
                parts=[Part(root=TextPart(text=text))],
            ),
        )

    def finalize(self, text: str = '') -> TaskArtifactUpdateEvent:
        """Create the final chunk event, closing the stream.

        Args:
            text: Optional final text content. Defaults to empty string.

        Returns:
            A ``TaskArtifactUpdateEvent`` with ``append=True`` and
            ``last_chunk=True``.

        Raises:
            RuntimeError: If ``finalize()`` has already been called.
        """
        if self._finalized:
            raise RuntimeError('finalize() has already been called.')
        self._finalized = True
        return TaskArtifactUpdateEvent(
            context_id=self._context_id,
            task_id=self._task_id,
            append=True,
            last_chunk=True,
            artifact=Artifact(
                artifact_id=self._artifact_id,
                name=self._name,
                description=self._description,
                parts=[Part(root=TextPart(text=text))],
            ),
        )
