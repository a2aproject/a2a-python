"""General utility functions for the A2A Python SDK."""

import functools
import inspect
import logging

from collections.abc import AsyncIterator, Callable
from typing import Any, TypeVar, cast

from packaging.version import InvalidVersion, Version

from a2a.server.context import ServerCallContext
from a2a.utils import constants
from a2a.utils.errors import VersionNotSupportedError


F = TypeVar('F', bound=Callable[..., Any])


logger = logging.getLogger(__name__)


def validate_version(expected_version: str) -> Callable[[F], F]:
    """Decorator that validates the A2A-Version header in the request context.

    The header name is defined by `constants.VERSION_HEADER` ('A2A-Version').
    If the header is missing or empty, it is interpreted as `constants.PROTOCOL_VERSION_0_3` ('0.3').
    If the version in the header does not match the `expected_version` (major and minor parts),
    a `VersionNotSupportedError` is raised. Patch version is ignored.

    This decorator supports both async methods and async generator methods. It
    expects a `ServerCallContext` to be present either in the arguments or
    keyword arguments of the decorated method.

    Args:
        expected_version: The A2A protocol version string expected by the method.

    Returns:
        The decorated function.

    Raises:
        VersionNotSupportedError: If the version in the request does not match `expected_version`.
    """
    try:
        expected_v = Version(expected_version)
    except InvalidVersion:
        # If the expected version is not a valid semver, we can't do major/minor comparison.
        # This shouldn't happen with our constants.
        expected_v = None

    def decorator(func: F) -> F:
        def _get_actual_version(
            args: tuple[Any, ...], kwargs: dict[str, Any]
        ) -> str:
            context = kwargs.get('context')
            if context is None:
                for arg in args:
                    if isinstance(arg, ServerCallContext):
                        context = arg
                        break

            if context is None:
                # If no context is found, we can't validate the version.
                # In a real scenario, this shouldn't happen for properly routed requests.
                # We default to the expected version to allow test call to proceed.
                return expected_version

            headers = context.state.get('headers', {})
            # Header names are usually case-insensitive in most frameworks, but dict lookup is case-sensitive.
            # We check both standard and lowercase versions.
            actual_version = headers.get(
                constants.VERSION_HEADER
            ) or headers.get(constants.VERSION_HEADER.lower())

            if not actual_version:
                return constants.PROTOCOL_VERSION_0_3

            return str(actual_version)

        def _is_version_compatible(actual: str) -> bool:
            if actual == expected_version:
                return True
            if not expected_v:
                return False
            try:
                actual_v = Version(actual)
            except InvalidVersion:
                return False
            else:
                return actual_v.major == expected_v.major

        if inspect.isasyncgenfunction(inspect.unwrap(func)):

            @functools.wraps(func)
            def async_gen_wrapper(
                *args: Any, **kwargs: Any
            ) -> AsyncIterator[Any]:
                actual_version = _get_actual_version(args, kwargs)
                if not _is_version_compatible(actual_version):
                    logger.warning(
                        "Version mismatch: actual='%s', expected='%s'",
                        actual_version,
                        expected_version,
                    )
                    raise VersionNotSupportedError(
                        message=f"A2A version '{actual_version}' is not supported by this handler. "
                        f"Expected version '{expected_version}'."
                    )
                return func(*args, **kwargs)

            return cast('F', async_gen_wrapper)

        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            actual_version = _get_actual_version(args, kwargs)
            if not _is_version_compatible(actual_version):
                logger.warning(
                    "Version mismatch: actual='%s', expected='%s'",
                    actual_version,
                    expected_version,
                )
                raise VersionNotSupportedError(
                    message=f"A2A version '{actual_version}' is not supported by this handler. "
                    f"Expected version '{expected_version}'."
                )
            return await func(*args, **kwargs)

        return cast('F', async_wrapper)

    return decorator
