"""Helper functions for the A2A client."""

from typing import Any
from uuid import uuid4

from a2a.types import Message, Part, Role, TextPart


def create_text_message_object(
    role: Role = Role.user,
    content: str = '',
    extensions: list[Any] | None = None,
    metadata: dict[Any, Any] | None = None,
) -> Message:
    """Create a Message object containing a single TextPart."""
    return Message(
        role=role,
        parts=[Part(TextPart(text=content or ''))],
        message_id=str(uuid4()),
        extensions=extensions or [],
        metadata=metadata or {},
    )
