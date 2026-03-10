from abc import ABC, abstractmethod

from a2a.client.client import ClientCallContext


class CredentialService(ABC):
    """An abstract service for retrieving credentials."""

    @abstractmethod
    async def get_credentials(
        self,
        security_scheme_name: str,
        context: ClientCallContext | None,
    ) -> str | None:
        """
        Retrieves a credential (e.g., token) for a security scheme.
        """


class InMemoryContextCredentialStore(CredentialService):
    """A simple in-memory store for session-keyed credentials.

    This class uses the 'sessionId' from the ClientCallContext state to
    store and retrieve credentials...
    """

    def __init__(self) -> None:
        self._store: dict[str, dict[str, str]] = {}

    async def get_credentials(
        self,
        security_scheme_name: str,
        context: ClientCallContext | None,
    ) -> str | None:
        """Retrieves credentials from the in-memory store.

        Args:
            security_scheme_name: The name of the security scheme.
            context: The client call context.

        Returns:
            The credential string, or None if not found.
        """
        if not context or 'sessionId' not in context.state:
            return None
        session_id = context.state['sessionId']
        return self._store.get(session_id, {}).get(security_scheme_name)

    async def set_credentials(
        self, session_id: str, security_scheme_name: str, credential: str
    ) -> None:
        """Method to populate the store."""
        if session_id not in self._store:
            self._store[session_id] = {}
        self._store[session_id][security_scheme_name] = credential
