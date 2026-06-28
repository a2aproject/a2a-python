"""Input sanitization utilities for A2A path parameters.

This module provides validation functions for resource IDs used in
REST URL paths, preventing injection of control characters, path
traversal sequences, and other unsafe inputs.
"""

import re

from a2a.utils.errors import InvalidRequestError


# Allowed characters for path resource IDs: alphanumeric, hyphen,
# underscore, and period.  This matches the character set typically
# used for UUIDs, task IDs, and push-notification config IDs.
_PATH_ID_PATTERN = re.compile(r'^[A-Za-z0-9._-]+$')

# ASCII control character boundaries.
_MAX_PRINTABLE_ASCII = 0x20  # First non-control character (space)
_DEL_ASCII = 0x7F  # DEL control character


def sanitize_path_id(value: str, param_name: str = 'id') -> str:
    """Validate and sanitize a path parameter used as a resource ID.

    Rejects values containing null bytes, newlines, other control
    characters, or any characters outside the safe set
    ``[A-Za-z0-9._-]``.

    Args:
        value: The raw path parameter value.
        param_name: Name of the parameter (for error messages).

    Returns:
        The validated value unchanged.

    Raises:
        InvalidRequestError: If the value contains disallowed characters
            or is empty.
    """
    if not value:
        raise InvalidRequestError(
            message=f'{param_name} must not be empty',
        )
    # Reject bare dot and double-dot to prevent path traversal.
    if value in ('.', '..'):
        raise InvalidRequestError(
            message=f'{param_name} cannot be "." or ".."',
        )
    # Reject null bytes and other control characters (0x00-0x1F, 0x7F).
    if any(
        ord(c) < _MAX_PRINTABLE_ASCII or ord(c) == _DEL_ASCII for c in value
    ):
        raise InvalidRequestError(
            message=f'{param_name} contains control characters',
        )
    if not _PATH_ID_PATTERN.match(value):
        raise InvalidRequestError(
            message=f'{param_name} contains invalid characters: {value!r}',
        )
    return value
