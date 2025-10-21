"""Utility functions for creating and handling A2A Message objects."""

import uuid

from a2a.types import (
    Message,
    Part,
    Role,
    TextPart,
)
from a2a.utils.parts import get_text_parts


def new_agent_text_message(
    text: str,
    context_id: str | None = None,
    task_id: str | None = None,
) -> Message:
    """Creates a new agent message containing a single TextPart.

    Args:
        text: The text content of the message.
        context_id: The context ID for the message.
        task_id: The task ID for the message.

    Returns:
        A new `Message` object with role 'agent'.
    """
    return Message(
        role=Role.agent,
        parts=[Part(root=TextPart(text=text))],
        message_id=str(uuid.uuid4()),
        task_id=task_id,
        context_id=context_id,
    )


def new_agent_parts_message(
    parts: list[Part],
    context_id: str | None = None,
    task_id: str | None = None,
) -> Message:
    """Creates a new agent message containing a list of Parts.

    Args:
        parts: The list of `Part` objects for the message content.
        context_id: The context ID for the message.
        task_id: The task ID for the message.

    Returns:
        A new `Message` object with role 'agent'.
    """
    return Message(
        role=Role.agent,
        parts=parts,
        message_id=str(uuid.uuid4()),
        task_id=task_id,
        context_id=context_id,
    )


def get_message_text(message: Message, delimiter: str = '\n') -> str:
    """Extracts and joins all text content from a Message's parts.

    Args:
        message: The `Message` object.
        delimiter: The string to use when joining text from multiple TextParts.

    Returns:
        A single string containing all text content, or an empty string if no text parts are found.
    """
    return delimiter.join(get_text_parts(message.parts))
