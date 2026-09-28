"""Shared policy for checking message parts against an agent's declared input modes."""

from a2a.types.a2a_pb2 import AgentCard, Message
from a2a.utils.errors import ContentTypeNotSupportedError


def validate_input_modes(message: Message, agent_card: AgentCard) -> None:
    """Rejects parts whose media type the agent card does not declare.

    The allowed set is the union of `default_input_modes` and every skill's
    `input_modes`, which may widen it: a skill's `input_modes` overrides the
    card default for that skill. A request does not name the skill that will
    serve it, so the union is the most that can be decided here -- anything
    outside it is supported by no skill and cannot be served, while a media
    type inside it may still reach a skill that does not take it, which only
    that skill can answer.

    A card declaring no input modes anywhere accepts everything: an empty
    union is an absent declaration, not an empty allowlist. `media_type` is
    a proto3 string, so a part that omits it arrives as `''` and is likewise
    not checked -- only a media type the client actually stated can
    contradict the card.
    """
    declared = set(agent_card.default_input_modes)
    for skill in agent_card.skills:
        declared.update(skill.input_modes)
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
