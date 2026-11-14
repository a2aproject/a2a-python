import pytest

from a2a.utils.errors import (
    A2AError,
    InvalidParamsError,
    TaskNotFoundError,
    VersionNotSupportedError,
)


class TestA2AErrorRepr:
    """Test cases for the __repr__ of A2AError and its subclasses."""

    @pytest.mark.parametrize(
        ('error', 'expected'),
        [
            (A2AError(), "A2AError(message='A2A Error', data=None)"),
            (
                TaskNotFoundError(),
                "TaskNotFoundError(message='Task not found', data=None)",
            ),
            (
                TaskNotFoundError('Task abc not found'),
                "TaskNotFoundError(message='Task abc not found', data=None)",
            ),
            (
                InvalidParamsError('Invalid field', data={'field': 'id'}),
                "InvalidParamsError(message='Invalid field', data={'field': 'id'})",
            ),
            (
                VersionNotSupportedError(data={'supported': ['1.0']}),
                "VersionNotSupportedError(message='Version not supported', "
                "data={'supported': ['1.0']})",
            ),
        ],
    )
    def test_repr(self, error: A2AError, expected: str) -> None:
        """Test that repr exposes message and data as keyword arguments."""
        assert repr(error) == expected

    def test_str_unchanged(self) -> None:
        """Test that __str__ still returns only the message."""
        error = InvalidParamsError('Invalid field', data={'field': 'id'})
        assert str(error) == 'Invalid field'
