"""Custom exceptions for the A2A client."""

from a2a.utils.errors import A2AError


class A2AClientError(A2AError):
    """Base exception for A2A Client errors."""


class AgentCardResolutionError(A2AClientError):
    """Exception raised when an agent card cannot be resolved."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code

    def __repr__(self) -> str:
        """Return a developer-friendly representation exposing the status code."""
        return (
            f'{self.__class__.__name__}('
            f'status_code={self.status_code!r}, '
            f'message={self.message!r})'
        )


class A2AClientTimeoutError(A2AClientError):
    """Exception for timeout errors during a request."""
