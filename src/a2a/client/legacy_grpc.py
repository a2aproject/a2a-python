"""Backwards compatibility layer for the legacy A2A gRPC client."""

import warnings

from a2a.client.transports.grpc import GrpcTransport
from a2a.grpc import a2a_pb2_grpc
from a2a.types import AgentCard


class A2AGrpcClient(GrpcTransport):
    """
    [DEPRECATED] Backwards compatibility wrapper for the gRPC client.
    """

    def __init__(
        self,
        grpc_stub: "a2a_pb2_grpc.A2AServiceStub",
        agent_card: AgentCard,
    ):
        warnings.warn(
            "A2AGrpcClient is deprecated and will be removed in a future version. "
            "Use ClientFactory to create a client with a gRPC transport.",
            DeprecationWarning,
            stacklevel=2,
        )
        super().__init__(grpc_stub, agent_card)