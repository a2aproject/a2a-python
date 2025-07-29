from unittest.mock import MagicMock

from a2a.server.apps.jsonrpc.fastapi_app import A2AFastAPIApplication
from a2a.server.apps.jsonrpc.jsonrpc_app import JSONRPCApplicationBuilder
from a2a.server.request_handlers.request_handler import RequestHandler
from a2a.types import AgentCard


class TestA2AFastAPIApplication:
    """
    Unit tests for the `A2AFastAPIApplication` implementation.
    """

    def test_builder_protocol(self):
        """
        Tests that `A2AFastAPIApplication` matches the `JSONRPCApplicationBuilder` protocol.

        This ensures that `A2AFastAPIApplication` can be used interchangeably where
        `JSONRPCApplicationBuilder` is expected.
        """
        card = MagicMock(spec=AgentCard)
        card.url = 'http://mockurl.com'
        card.supports_authenticated_extended_card = False
        handler = MagicMock(spec=RequestHandler)
        builder = A2AFastAPIApplication(card, handler)
        assert isinstance(builder, JSONRPCApplicationBuilder)
