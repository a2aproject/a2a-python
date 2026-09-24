"""Shared policy for checking message parts against an agent's declared input modes."""

from a2a.types.a2a_pb2 import AgentCard, Message
from a2a.utils.errors import ContentTypeNotSupportedError


def validate_input_modes(message: Message, agent_card: AgentCard) -> None:
    """Rejects parts whose media type the agent card does not declare.

    Spec 3.3.2 defines ContentTypeNotSupportedError for a media type in the
    request's message parts that the agent does not support.

    A card declaring no input modes accepts everything: an empty
    `default_input_modes` is an absent declaration, not an empty allowlist.
    `media_type` is a proto3 string, so a part that omits it arrives as `''`
    and is likewise not checked -- only a media type the client actually
    stated can contradict the card.
    """
    declared = set(agent_card.default_input_modes)
    if not declared:
        return

    for part in message.parts:
        if part.media_type and part.media_type not in declared:
            raise ContentTypeNotSupportedError(
                message=(
                    f'Media type {part.media_type} is not supported. '
                    f'Supported input modes: {", ".join(sorted(declared))}'
                )
            )
