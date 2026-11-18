"""Shared policy for checking a request against an agent's required extensions."""

from a2a.types.a2a_pb2 import AgentCard
from a2a.utils.errors import ExtensionSupportRequiredError


def validate_required_extensions(
    requested_extensions: set[str], agent_card: AgentCard
) -> None:
    """Rejects a request that does not declare every required extension.

    An extension the card marks `required` must appear in the URIs the
    client sent in the `A2A-Extensions` header, or the agent MUST answer
    with `ExtensionSupportRequiredError` (spec section 3.3.4). URIs are
    compared exactly.
    """
    missing = [
        extension.uri
        for extension in agent_card.capabilities.extensions
        if extension.required and extension.uri not in requested_extensions
    ]
    if missing:
        raise ExtensionSupportRequiredError(
            message=(f'Required extensions not requested: {", ".join(missing)}')
        )
