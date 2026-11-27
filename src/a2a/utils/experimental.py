"""Marks APIs that may change without a deprecation period."""

from typing import TypeVar


_T = TypeVar('_T')

_NOTE = (
    '\n\nExperimental: this API may change or be removed in any release, '
    'without a deprecation period.'
)


def experimental(obj: _T) -> _T:
    """Marks a class or function as experimental, leaving it unchanged."""
    obj.__doc__ = (obj.__doc__ or '') + _NOTE
    setattr(obj, '__a2a_experimental__', True)  # noqa: B010
    return obj
