"""Custom exceptions for the A2A client."""

from a2a.utils.errors import A2AError


class A2AClientError(A2AError):
    """Base exception for A2A Client errors."""


class AgentCardResolutionError(A2AClientError):
    """Exception raised when an agent card cannot be resolved."""
