"""Utility functions for creating and handling A2A Parts objects."""

from typing import Any

from a2a.types import (
    DataPart,
    FilePart,
    FileWithBytes,
    FileWithUri,
    Part,
    TextPart,
)


def get_text_parts(parts: list[Part]) -> list[str]:
    """Extracts text content from all TextPart objects in a list of Parts.

    Args:
        parts: A list of `Part` objects.

    Returns:
        A list of strings containing the text content from any `TextPart` objects found.
    """
    return [part.root.text for part in parts if isinstance(part.root, TextPart)]


def get_data_parts(parts: list[Part]) -> list[dict[str, Any]]:
    """Extracts dictionary data from all DataPart objects in a list of Parts.

    Args:
        parts: A list of `Part` objects.

    Returns:
        A list of dictionaries containing the data from any `DataPart` objects found.
    """
    return [part.root.data for part in parts if isinstance(part.root, DataPart)]


def get_file_parts(parts: list[Part]) -> list[FileWithBytes | FileWithUri]:
    """Extracts file data from all FilePart objects in a list of Parts.

    Args:
        parts: A list of `Part` objects.

    Returns:
        A list of `FileWithBytes` or `FileWithUri` objects containing the file data from any `FilePart` objects found.
    """
    return [part.root.file for part in parts if isinstance(part.root, FilePart)]
