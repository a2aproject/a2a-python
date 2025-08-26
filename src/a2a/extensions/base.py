from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from a2a.client.client import A2AClient
    from a2a.server.server import A2AServer


class Extension:
    """Base class for all extensions."""

    def __init__(self, **kwargs: Any) -> None:
        ...

    def on_client_message(self, message: Any) -> None:
        """Called when a message is sent from the client."""
        ...

    def on_server_message(self, message: Any) -> None:
        """Called when a message is received by the server."""
        ...

    def install(self, client_or_server: A2AClient | A2AServer) -> None:
        """Called when the extension is installed on a client or server."""
        ...
