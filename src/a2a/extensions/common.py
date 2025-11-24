from typing import Any

from a2a.types import AgentCard, AgentExtension


HTTP_EXTENSION_HEADER = 'X-A2A-Extensions'


def get_requested_extensions(values: list[str]) -> set[str]:
    """Get the set of requested extensions from an input list.

    This handles the list containing potentially comma-separated values, as
    occurs when using a list in an HTTP header.
    """
    return {
        stripped
        for v in values
        for ext in v.split(',')
        if (stripped := ext.strip())
    }


def find_extension_by_uri(card: AgentCard, uri: str) -> AgentExtension | None:
    """Find an AgentExtension in an AgentCard given a uri."""
    for ext in card.capabilities.extensions or []:
        if ext.uri == uri:
            return ext

    return None


def update_extension_header(
    http_kwargs: dict[str, Any] | None,
    extensions: list[str] | None,
) -> dict[str, Any]:
    """Update the X-A2A-Extensions header with active extensions."""
    http_kwargs = http_kwargs or {}
    if extensions is not None:
        headers = http_kwargs.setdefault('headers', {})
        headers[HTTP_EXTENSION_HEADER] = ','.join(extensions)
    return http_kwargs
