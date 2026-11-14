import pytest

from a2a.client import (
    A2AClientError,
    A2AClientTimeoutError,
    AgentCardResolutionError,
)


class TestA2AClientError:
    """Test cases for the base A2AClientError class."""

    def test_instantiation(self) -> None:
        """Test that A2AClientError can be instantiated."""
        error = A2AClientError('Test error message')
        assert isinstance(error, Exception)
        assert str(error) == 'Test error message'

    def test_inheritance(self) -> None:
        """Test that A2AClientError inherits from Exception."""
        error = A2AClientError()
        assert isinstance(error, Exception)

    def test_raising_base_error(self) -> None:
        """Test raising the base error."""
        with pytest.raises(A2AClientError) as excinfo:
            raise A2AClientError('Generic client error')

        assert str(excinfo.value) == 'Generic client error'


class TestClientErrorRepr:
    """Test cases for the __repr__ of client exception classes."""

    @pytest.mark.parametrize(
        ('error', 'expected'),
        [
            (
                A2AClientError('Network communication error'),
                "A2AClientError(message='Network communication error', data=None)",
            ),
            (
                A2AClientTimeoutError('Client Request timed out'),
                "A2AClientTimeoutError(message='Client Request timed out', data=None)",
            ),
            (
                AgentCardResolutionError('Failed to fetch agent card', 404),
                'AgentCardResolutionError(status_code=404, '
                "message='Failed to fetch agent card')",
            ),
            (
                AgentCardResolutionError('Invalid agent card JSON'),
                'AgentCardResolutionError(status_code=None, '
                "message='Invalid agent card JSON')",
            ),
        ],
    )
    def test_repr(self, error: A2AClientError, expected: str) -> None:
        """Test that repr exposes structured attributes as keyword arguments."""
        assert repr(error) == expected
