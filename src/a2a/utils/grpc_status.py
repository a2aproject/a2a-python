import logging

from typing import Any, NamedTuple


try:
    import grpc  # type: ignore[reportMissingModuleSource]
except ImportError:
    grpc = None  # type: ignore

from google.rpc import status_pb2  # type: ignore[reportMissingModuleSource]


logger = logging.getLogger(__name__)

GRPC_STATUS_DETAILS_BIN_KEY = 'grpc-status-details-bin'


class GrpcStatus(NamedTuple):
    """Represents the gRPC status code, details, and trailing metadata."""

    code: Any
    details: str
    trailing_metadata: tuple


def status_to_grpc(status: status_pb2.Status) -> GrpcStatus:
    """Converts a google.rpc.status.Status message into its gRPC components."""
    if grpc is None:
        raise ImportError(
            'gRPC is not installed. Install with: pip install a2a-sdk[grpc]'
        )
    for x in grpc.StatusCode:
        if x.value[0] == status.code:
            grpc_code = x
            break
    else:
        raise ValueError(f'Invalid status code {status.code}')

    bin_data = status.SerializeToString()
    metadata = ((GRPC_STATUS_DETAILS_BIN_KEY, bin_data),)

    return GrpcStatus(grpc_code, status.message, metadata)


def status_from_call(call: Any) -> status_pb2.Status | None:
    """Extracts a google.rpc.status.Status message from a grpc.Call instance."""
    if grpc is None:
        raise ImportError(
            'gRPC is not installed. Install with: pip install a2a-sdk[grpc]'
        )
    if not hasattr(call, 'trailing_metadata'):
        return None
    trailing_metadata = call.trailing_metadata()
    if not trailing_metadata:
        return None
    for k, v in trailing_metadata:
        if k == GRPC_STATUS_DETAILS_BIN_KEY:
            status = status_pb2.Status()
            status.ParseFromString(v)

            if call.code().value[0] != status.code:
                raise ValueError(
                    f'Mismatched status code: proto={status.code}, call={call.code()}'
                )
            if call.details() != status.message:
                raise ValueError(
                    f'Mismatched status message: proto={status.message!r}, call={call.details()!r}'
                )
            return status
    return None
