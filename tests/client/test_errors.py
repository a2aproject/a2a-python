import pytest

from a2a.client import A2AClientError


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
