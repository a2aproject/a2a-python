"""Helper functions for the A2A client."""

from uuid import uuid4

from a2a.types.a2a_pb2 import Message, Part, Role


def create_text_message_object(
    role: Role = Role.ROLE_USER, content: str = ''
) -> Message:
    """Create a Message object containing a single text Part.

    Args:
        role: The role of the message sender (user or agent). Defaults to Role.ROLE_USER.
        content: The text content of the message. Defaults to an empty string.

    Returns:
        A `Message` object with a new UUID message_id.
    """
    return Message(
        role=role, parts=[Part(text=content)], message_id=str(uuid4())
    )
