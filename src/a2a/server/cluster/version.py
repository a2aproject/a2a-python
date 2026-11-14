from typing import ClassVar


class TaskVersion:
    """A version marker a `VersionedTaskStore` assigns to a stored `Task`.

    Prevents concurrent state re-writes. The wrapped value is the store's
    choice - a counter, a commit timestamp, etc. Callers do not read it or do
    arithmetic on it; they only pass it back to `save` and order two versions
    with `is_after`.
    """

    __slots__ = ('_value',)

    MISSING: 'ClassVar[TaskVersion]'

    def __init__(self, value: int | str) -> None:
        self._value = value

    def __eq__(self, other: object) -> bool:
        """Two versions are equal when they wrap equal values."""
        return isinstance(other, TaskVersion) and self._value == other._value

    def __hash__(self) -> int:
        """Hash by the wrapped value so versions are usable as keys."""
        return hash(self._value)

    def __repr__(self) -> str:
        """Render `MISSING` specially, otherwise show the wrapped value."""
        if self.is_missing:
            return 'TaskVersion.MISSING'
        return f'TaskVersion({self._value!r})'

    @property
    def is_missing(self) -> bool:
        """Whether this token means "versioning is not tracked"."""
        return self._value == 0

    def is_after(self, other: 'TaskVersion') -> bool:
        """Whether `self` is a strictly later version than `other`."""
        if other.is_missing:
            return True
        if self.is_missing:
            return False
        if type(self._value) is not type(other._value):  # noqa: SLF001
            raise TypeError(
                'Cannot compare TaskVersions with different value types: '
                f'{type(other._value).__name__} and '  # noqa: SLF001
                f'{type(self._value).__name__}'
            )
        return other._value < self._value  # ty:ignore[unsupported-operator]  # noqa: SLF001


TaskVersion.MISSING = TaskVersion(0)
