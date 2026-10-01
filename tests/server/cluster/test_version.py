"""Tests for `TaskVersion`."""

import pytest

from a2a.server.cluster import TaskVersion


def test_missing_is_missing() -> None:
    assert TaskVersion.MISSING.is_missing is True


def test_real_version_is_not_missing() -> None:
    assert TaskVersion(1).is_missing is False
    assert TaskVersion('etag-abc').is_missing is False


def test_zero_int_is_missing() -> None:
    # 0 is the reserved "not tracked" value.
    assert TaskVersion(0).is_missing is True
    assert TaskVersion(0) == TaskVersion.MISSING


def test_string_zero_is_not_missing() -> None:
    # '0' != 0 in Python, so a string ETag of '0' is a real version.
    assert TaskVersion('0').is_missing is False


def test_equality_by_value() -> None:
    assert TaskVersion(5) == TaskVersion(5)
    assert TaskVersion(5) != TaskVersion(6)
    assert TaskVersion('a') == TaskVersion('a')
    assert TaskVersion('a') != TaskVersion('b')


def test_equality_with_non_taskversion() -> None:
    assert TaskVersion(5) != 5
    assert TaskVersion(5) != 'TaskVersion(5)'
    assert (TaskVersion(5) == object()) is False


def test_hashable_and_usable_in_sets_and_dicts() -> None:
    versions = {TaskVersion(1), TaskVersion(1), TaskVersion(2)}
    assert versions == {TaskVersion(1), TaskVersion(2)}

    mapping = {TaskVersion('x'): 'value'}
    assert mapping[TaskVersion('x')] == 'value'


def test_repr() -> None:
    assert repr(TaskVersion.MISSING) == 'TaskVersion.MISSING'
    assert repr(TaskVersion(0)) == 'TaskVersion.MISSING'
    assert repr(TaskVersion(7)) == 'TaskVersion(7)'
    assert repr(TaskVersion('etag')) == "TaskVersion('etag')"


def test_is_after_ordering_integers() -> None:
    assert TaskVersion(2).is_after(TaskVersion(1)) is True
    assert TaskVersion(1).is_after(TaskVersion(2)) is False


def test_is_after_equal_versions_is_false() -> None:
    # "strictly later" -> equal is not after.
    assert TaskVersion(1).is_after(TaskVersion(1)) is False


def test_is_after_missing_asymmetry() -> None:
    # Anything real is "after" MISSING (untracked baseline is oldest).
    assert TaskVersion(1).is_after(TaskVersion.MISSING) is True
    # MISSING is never after a real version.
    assert TaskVersion.MISSING.is_after(TaskVersion(1)) is False


def test_is_after_both_missing_is_false() -> None:
    # other.is_missing short-circuits to True per the contract.
    assert TaskVersion.MISSING.is_after(TaskVersion.MISSING) is True


def test_is_after_string_versions() -> None:
    # Works for orderable non-int values too (e.g. zero-padded ETags).
    assert TaskVersion('0002').is_after(TaskVersion('0001')) is True
    assert TaskVersion('0001').is_after(TaskVersion('0002')) is False


def test_missing_is_a_singleton_value() -> None:
    # Not necessarily the same object, but value-equal and both missing.
    assert TaskVersion(0) == TaskVersion.MISSING
    assert TaskVersion(0).is_missing and TaskVersion.MISSING.is_missing


def test_is_after_mismatched_value_types_raises() -> None:
    # A store uses one value type consistently; comparing an int-backed
    # version against a str-backed one is a programming error.
    with pytest.raises(TypeError, match='different value types'):
        TaskVersion(1).is_after(TaskVersion('a'))
    with pytest.raises(TypeError, match='different value types'):
        TaskVersion('a').is_after(TaskVersion(1))


def test_is_after_mismatch_guard_not_triggered_when_either_missing() -> None:
    # MISSING short-circuits before the type check, so no TypeError.
    assert TaskVersion('a').is_after(TaskVersion.MISSING) is True
    assert TaskVersion.MISSING.is_after(TaskVersion(1)) is False
