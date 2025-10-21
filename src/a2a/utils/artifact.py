"""Utility functions for creating A2A Artifact objects."""

import uuid

from typing import Any

from a2a.types import Artifact, DataPart, Part, TextPart
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
