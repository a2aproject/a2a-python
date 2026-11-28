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


def test_permission_denied_error_mappings() -> None:
    """PermissionDeniedError is exported and mapped across HTTP, gRPC, and JSON-RPC."""
    import grpc

    from a2a import types as a2a_types
    from a2a.server.request_handlers.grpc_handler import _ERROR_CODE_MAP
    from a2a.server.request_handlers.response_helpers import EXCEPTION_MAP
    from a2a.utils.errors import (
        A2A_ERROR_MAPPING,
        A2A_ERROR_REASONS,
        A2A_REASON_TO_ERROR,
        JSON_RPC_ERROR_CODE_MAP,
        ErrorMapping,
        PermissionDeniedError,
    )

    assert a2a_types.PermissionDeniedError is PermissionDeniedError
    err = PermissionDeniedError(
        'Missing required scope', data={'missing_scopes': ['tasks:cancel']}
    )
    assert repr(err) == (
        "PermissionDeniedError(message='Missing required scope', "
        "data={'missing_scopes': ['tasks:cancel']})"
    )
    assert A2A_ERROR_MAPPING[PermissionDeniedError] == ErrorMapping(
        403, 'PERMISSION_DENIED', 'PERMISSION_DENIED'
    )
    assert A2A_ERROR_REASONS[PermissionDeniedError] == 'PERMISSION_DENIED'
    assert A2A_REASON_TO_ERROR['PERMISSION_DENIED'] is PermissionDeniedError
    assert JSON_RPC_ERROR_CODE_MAP[PermissionDeniedError] == -32010
    assert _ERROR_CODE_MAP[PermissionDeniedError] == grpc.StatusCode.PERMISSION_DENIED
    assert PermissionDeniedError in EXCEPTION_MAP
