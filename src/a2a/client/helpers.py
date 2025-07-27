"""Helper functions for the A2A client."""

from uuid import uuid4

from a2a.types import Message, Part, Role, TextPart


def create_text_message_object(
    role: Role = Role.user, content: str = ''
) -> Message:
    """Create a Message object containing a single TextPart.

    Args:
        role: The role of the message sender (user or agent). Defaults to Role.user.
        content: The text content of the message. Defaults to an empty string.

    Returns:
        A `Message` object with a new UUID message_id.
    """
    return Message(
        role=role, parts=[Part(TextPart(text=content))], message_id=str(uuid4())
    )
