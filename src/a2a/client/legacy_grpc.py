"""Backwards compatibility layer for the legacy A2A gRPC client."""

import warnings

from typing import TYPE_CHECKING

from a2a.client.transports.grpc import GrpcTransport
from a2a.types import AgentCard


if TYPE_CHECKING:
    from a2a.grpc.a2a_pb2_grpc import A2AServiceStub


class A2AGrpcClient(GrpcTransport):
    """[DEPRECATED] Backwards compatibility wrapper for the gRPC client."""

    def __init__(
        self,
        grpc_stub: 'A2AServiceStub',
        agent_card: AgentCard,
    ):
        warnings.warn(
            'A2AGrpcClient is deprecated and will be removed in a future version. '
            'Use ClientFactory to create a client with a gRPC transport.',
            DeprecationWarning,
            stacklevel=2,
        )
        super().__init__(grpc_stub, agent_card)
