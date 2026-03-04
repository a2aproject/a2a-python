"""Custom exceptions for the A2A client."""

from a2a.utils.errors import A2AError


class A2AClientError(A2AError):
    """Base exception for A2A Client errors."""


class AgentCardResolutionError(A2AClientError):
    """Exception raised when an agent card cannot be resolved."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class A2AClientTimeoutError(A2AClientError):
    """Exception for timeout errors during a request."""
