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

    Media types are compared on type and subtype alone, ignoring case
    (RFC 2045 section 5.1) and parameters, so `Text/Plain; charset=utf-8`
    matches a card that declares `text/plain`. A declared media range
    (RFC 9110 section 12.5.1) matches by type: `image/*` accepts `image/png`
    but not `video/mp4`, and `*/*` accepts any media type.
    """
    modes = set(agent_card.default_input_modes)
    for skill in agent_card.skills:
        modes.update(skill.input_modes)
    declared = {_essence(mode) for mode in modes}
    if not declared:
        return

    for part in message.parts:
        if part.media_type and not _is_declared(
            _essence(part.media_type), declared
        ):
            raise ContentTypeNotSupportedError(
                message=(
                    f'Media type {part.media_type} is not supported. '
                    f'Supported input modes: {", ".join(sorted(modes))}'
                )
            )


def _is_declared(essence: str, declared: set[str]) -> bool:
    """Returns whether `essence` is declared exactly or by a media range."""
    if essence in declared or '*/*' in declared:
        return True
    media_type = essence.split('/', 1)[0]
    return f'{media_type}/*' in declared


def _essence(media_type: str) -> str:
    """Returns `type/subtype` in lower case, without parameters."""
    return media_type.split(';', 1)[0].strip().lower()
