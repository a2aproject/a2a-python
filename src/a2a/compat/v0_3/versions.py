"""Utility functions for protocol version comparison and validation."""

from packaging.version import InvalidVersion, Version

from a2a.utils.constants import PROTOCOL_VERSION_0_3, PROTOCOL_VERSION_1_0


def is_legacy_version(version: str | None) -> bool:
    """Determines if the given version is a legacy protocol version (>=0.3 and <1.0)."""
    if not version:
        return False
    try:
        v = Version(version)
        return (
            Version(PROTOCOL_VERSION_0_3) <= v < Version(PROTOCOL_VERSION_1_0)
        )
    except InvalidVersion:
        return False
