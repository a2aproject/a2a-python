import grpc
import pytest

from a2a.utils.grpc_status import (
    GRPC_STATUS_DETAILS_BIN_KEY,
    status_from_call,
    status_to_grpc,
)
from google.protobuf import any_pb2
from google.rpc import error_details_pb2, status_pb2


class MockGrpcCall:
    """Mock for a gRPC Call object simulating trailing metadata and status info."""

    def __init__(
        self, code: grpc.StatusCode, details: str, trailing_metadata: tuple
    ):
        self._code = code
        self._details = details
        self._trailing_metadata = trailing_metadata

    def code(self) -> grpc.StatusCode:
        return self._code

    def details(self) -> str:
        return self._details

    def trailing_metadata(self) -> tuple:
        return self._trailing_metadata


def test_status_to_grpc_success():
    """Test that a Status protobuf is correctly mapped to GrpcStatus namedtuple."""
    status = status_pb2.Status(
        code=grpc.StatusCode.INVALID_ARGUMENT.value[0],
        message='Invalid parameter provided',
    )

    grpc_status = status_to_grpc(status)

    assert grpc_status.code == grpc.StatusCode.INVALID_ARGUMENT
    assert grpc_status.details == 'Invalid parameter provided'
    assert len(grpc_status.trailing_metadata) == 1

    key, val = grpc_status.trailing_metadata[0]
    assert key == GRPC_STATUS_DETAILS_BIN_KEY

    # Parse back the serialized bytes to verify content
    parsed_status = status_pb2.Status()
    parsed_status.ParseFromString(val)
    assert parsed_status.code == status.code
    assert parsed_status.message == status.message


def test_status_to_grpc_invalid_code():
    """Test that ValueError is raised for an invalid gRPC status code value."""
    status = status_pb2.Status(code=999, message='Bad status code')
    with pytest.raises(ValueError, match='Invalid status code 999'):
        status_to_grpc(status)


def test_status_from_call_no_metadata():
    """Test that status_from_call returns None if trailing metadata is missing."""
    call = MockGrpcCall(grpc.StatusCode.OK, 'OK', ())
    assert status_from_call(call) is None


def test_status_from_call_success_roundtrip():
    """Test standard roundtrip: Status -> status_to_grpc -> Call -> status_from_call."""
    # 1. Create a status with error details
    status = status_pb2.Status(
        code=grpc.StatusCode.NOT_FOUND.value[0],
        message='Task not found',
    )
    error_info = error_details_pb2.ErrorInfo(
        reason='TASK_NOT_FOUND',
        domain='a2a-protocol.org',
    )
    detail = any_pb2.Any()
    detail.Pack(error_info)
    status.details.append(detail)

    # 2. Convert to gRPC components
    grpc_status = status_to_grpc(status)

    # 3. Wrap in a mock Call object
    call = MockGrpcCall(
        code=grpc_status.code,
        details=grpc_status.details,
        trailing_metadata=grpc_status.trailing_metadata,
    )

    # 4. Parse back using status_from_call
    parsed_status = status_from_call(call)

    assert parsed_status is not None
    assert parsed_status.code == status.code
    assert parsed_status.message == status.message
    assert len(parsed_status.details) == 1

    parsed_detail = error_details_pb2.ErrorInfo()
    parsed_status.details[0].Unpack(parsed_detail)
    assert parsed_detail.reason == 'TASK_NOT_FOUND'
    assert parsed_detail.domain == 'a2a-protocol.org'


def test_status_from_call_mismatched_code():
    """Test that ValueError is raised if call status code doesn't match parsed status code."""
    status = status_pb2.Status(
        code=grpc.StatusCode.INVALID_ARGUMENT.value[0],
        message='Mismatched status code test',
    )
    grpc_status = status_to_grpc(status)

    # Intentionally change the mock Call code to StatusCode.UNAUTHENTICATED
    call = MockGrpcCall(
        code=grpc.StatusCode.UNAUTHENTICATED,
        details=grpc_status.details,
        trailing_metadata=grpc_status.trailing_metadata,
    )

    with pytest.raises(ValueError, match='Mismatched status code'):
        status_from_call(call)


def test_status_from_call_mismatched_message():
    """Test that ValueError is raised if call details message doesn't match parsed status message."""
    status = status_pb2.Status(
        code=grpc.StatusCode.INVALID_ARGUMENT.value[0],
        message='Mismatched status message test',
    )
    grpc_status = status_to_grpc(status)

    # Intentionally change the mock Call details message
    call = MockGrpcCall(
        code=grpc_status.code,
        details='A completely different message',
        trailing_metadata=grpc_status.trailing_metadata,
    )

    with pytest.raises(ValueError, match='Mismatched status message'):
        status_from_call(call)
