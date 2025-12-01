"""Utility functions for creating and handling A2A Parts objects."""

from collections.abc import Sequence
from typing import Any

from google.protobuf.json_format import MessageToDict

from a2a.types.a2a_pb2 import (
    FilePart,
    Part,
)


def get_text_parts(parts: Sequence[Part]) -> list[str]:
    """Extracts text content from all text Parts.

    Args:
        parts: A sequence of `Part` objects.

    Returns:
        A list of strings containing the text content from any text Parts found.
    """
    return [part.text for part in parts if part.HasField('text')]


def get_data_parts(parts: Sequence[Part]) -> list[dict[str, Any]]:
    """Extracts dictionary data from all DataPart objects in a list of Parts.

    Args:
        parts: A sequence of `Part` objects.

    Returns:
        A list of dictionaries containing the data from any `DataPart` objects found.
    """
    return [
        MessageToDict(part.data.data) for part in parts if part.HasField('data')
    ]


def get_file_parts(parts: Sequence[Part]) -> list[FilePart]:
    """Extracts file data from all FilePart objects in a list of Parts.

    Args:
        parts: A sequence of `Part` objects.

    Returns:
        A list of `FilePart` objects containing the file data from any `FilePart` objects found.
    """
    return [part.file for part in parts if part.HasField('file')]
