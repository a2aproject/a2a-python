"""Backwards compatibility layer for the legacy A2A gRPC client."""

import warnings

from typing import TYPE_CHECKING

from a2a.client.transports.grpc import GrpcTransport
from a2a.types import AgentCard


if TYPE_CHECKING:
    from a2a.grpc.a2a_pb2_grpc import A2AServiceStub


class A2AGrpcClient(GrpcTransport):
    """[DEPRECATED] Backwards compatibility wrapper for the gRPC client."""

    def __init__(  # pylint: disable=super-init-not-called
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
        # The old gRPC client accepted a stub directly. The new one accepts a
        # channel and builds the stub itself. We just have a stub here, so we
        # need to handle initialization ourselves.
        self.stub = grpc_stub
        self.agent_card = agent_card
        self._needs_extended_card = (
            agent_card.supports_authenticated_extended_card
            if agent_card
            else True
        )

        class _NopChannel:
            async def close(self) -> None:
                pass

        self.channel = _NopChannel()
