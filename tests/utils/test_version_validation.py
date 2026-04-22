"""Tests for version validation decorators."""

import pytest
from unittest.mock import MagicMock

from a2a.server.context import ServerCallContext
from a2a.utils import constants
from a2a.utils.errors import VersionNotSupportedError
from a2a.utils.version_validator import validate_version


class TestHandler:
    @validate_version(constants.PROTOCOL_VERSION_1_0)
    async def async_method(self, request, context: ServerCallContext):
        return 'success'

    @validate_version(constants.PROTOCOL_VERSION_1_0)
    async def async_gen_method(self, request, context: ServerCallContext):
        yield 'success'

    @validate_version(constants.PROTOCOL_VERSION_0_3)
    async def compat_method(self, request, context: ServerCallContext):
        return 'success'


@pytest.mark.asyncio
async def test_validate_version_success():
    handler = TestHandler()
    context = ServerCallContext(
        state={'headers': {constants.VERSION_HEADER: '1.0'}}
    )

    result = await handler.async_method(None, context)
    assert result == 'success'


@pytest.mark.asyncio
async def test_validate_version_case_insensitive():
    handler = TestHandler()
    # Test lowercase header name
    context = ServerCallContext(
        state={'headers': {constants.VERSION_HEADER.lower(): '1.0'}}
    )

    result = await handler.async_method(None, context)
    assert result == 'success'


@pytest.mark.asyncio
async def test_validate_version_mismatch():
    handler = TestHandler()
    context = ServerCallContext(
        state={'headers': {constants.VERSION_HEADER: '0.3'}}
    )

    with pytest.raises(VersionNotSupportedError) as excinfo:
        await handler.async_method(None, context)
    assert "A2A version '0.3' is not supported" in str(excinfo.value)


@pytest.mark.asyncio
async def test_validate_version_missing_defaults_to_0_3():
    handler = TestHandler()
    context = ServerCallContext(state={'headers': {}})

    # Missing header should be interpreted as 0.3.
    # Since async_method expects 1.0, it should fail.
    with pytest.raises(VersionNotSupportedError) as excinfo:
        await handler.async_method(None, context)
    assert "A2A version '0.3' is not supported" in str(excinfo.value)

    # But compat_method expects 0.3, so it should succeed.
    result = await handler.compat_method(None, context)
    assert result == 'success'


@pytest.mark.asyncio
async def test_validate_version_async_gen_success():
    handler = TestHandler()
    context = ServerCallContext(
        state={'headers': {constants.VERSION_HEADER: '1.0'}}
    )

    results = []
    async for item in handler.async_gen_method(None, context):
        results.append(item)

    assert results == ['success']


@pytest.mark.asyncio
async def test_validate_version_async_gen_failure():
    handler = TestHandler()
    context = ServerCallContext(
        state={'headers': {constants.VERSION_HEADER: '0.3'}}
    )

    with pytest.raises(VersionNotSupportedError):
        async for _ in handler.async_gen_method(None, context):
            pass


@pytest.mark.asyncio
async def test_validate_version_no_context():
    handler = TestHandler()

    # If no context is found, it should default to allowing the call (for safety/backward compatibility with non-context methods)
    # although in our actual handlers context will be there.
    result = await handler.async_method(None, None)
    assert result == 'success'


@pytest.mark.asyncio
async def test_validate_version_ignore_minor_patch():
    handler = TestHandler()

    # 1.0.1 should match 1.0
    context_patch = ServerCallContext(
        state={'headers': {constants.VERSION_HEADER: '1.0.1'}}
    )
    result = await handler.async_method(None, context_patch)
    assert result == 'success'

    # 1.0.0 should match 1.0
    context_zero_patch = ServerCallContext(
        state={'headers': {constants.VERSION_HEADER: '1.0.0'}}
    )
    result = await handler.async_method(None, context_zero_patch)
    assert result == 'success'

    # 1.1.0 should match 1.0
    context_diff_minor = ServerCallContext(
        state={'headers': {constants.VERSION_HEADER: '1.1.0'}}
    )
    result = await handler.async_method(None, context_diff_minor)
    assert result == 'success'

    # 2.0.0 should NOT match 1.0
    context_diff_major = ServerCallContext(
        state={'headers': {constants.VERSION_HEADER: '2.0.0'}}
    )
    with pytest.raises(VersionNotSupportedError):
        await handler.async_method(None, context_diff_major)


@pytest.mark.asyncio
async def test_validate_version_handler_expects_patch():
    class PatchHandler:
        @validate_version('1.0.2')
        async def method(self, request, context: ServerCallContext):
            return 'success'

    handler = PatchHandler()

    # 1.0 should match 1.0.2
    context_no_patch = ServerCallContext(
        state={'headers': {constants.VERSION_HEADER: '1.0'}}
    )
    result = await handler.method(None, context_no_patch)
    assert result == 'success'

    # 1.0.5 should match 1.0.2
    context_diff_patch = ServerCallContext(
        state={'headers': {constants.VERSION_HEADER: '1.0.5'}}
    )
    result = await handler.method(None, context_diff_patch)
    assert result == 'success'
