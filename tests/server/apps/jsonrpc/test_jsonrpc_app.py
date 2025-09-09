from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from starlette.testclient import TestClient


# Attempt to import StarletteBaseUser, fallback to MagicMock if not available
try:
    from starlette.authentication import BaseUser as StarletteBaseUser
except ImportError:
    StarletteBaseUser = MagicMock()  # type: ignore

from a2a.extensions.common import HTTP_EXTENSION_HEADER
from a2a.server.apps.jsonrpc import (
    jsonrpc_app,  # Keep this import for optional deps test
)
from a2a.server.apps.jsonrpc.jsonrpc_app import (
    JSONRPCApplication,
    StarletteUserProxy,
)
from a2a.server.apps.jsonrpc.starlette_app import A2AStarletteApplication
from a2a.server.context import ServerCallContext
from a2a.server.request_handlers.request_handler import (
    RequestHandler,
)  # For mock spec
from a2a.types import (
    AgentCard,
    Message,
    MessageSendParams,
    Part,
    Role,
    SendMessageRequest,
    SendMessageResponse,
    SendMessageSuccessResponse,
    TextPart,
)


# --- StarletteUserProxy Tests ---


class TestStarletteUserProxy:
    def test_starlette_user_proxy_is_authenticated_true(self):
        starlette_user_mock = MagicMock(spec=StarletteBaseUser)
        starlette_user_mock.is_authenticated = True
        proxy = StarletteUserProxy(starlette_user_mock)
        assert proxy.is_authenticated is True

    def test_starlette_user_proxy_is_authenticated_false(self):
        starlette_user_mock = MagicMock(spec=StarletteBaseUser)
        starlette_user_mock.is_authenticated = False
        proxy = StarletteUserProxy(starlette_user_mock)
        assert proxy.is_authenticated is False

    def test_starlette_user_proxy_user_name(self):
        starlette_user_mock = MagicMock(spec=StarletteBaseUser)
        starlette_user_mock.display_name = 'Test User DisplayName'
        proxy = StarletteUserProxy(starlette_user_mock)
        assert proxy.user_name == 'Test User DisplayName'

    def test_starlette_user_proxy_user_name_raises_attribute_error(self):
        """
        Tests that if the underlying starlette user object is missing the
        display_name attribute, the proxy currently raises an AttributeError.
        """
        starlette_user_mock = MagicMock(spec=StarletteBaseUser)
        # Ensure display_name is not present on the mock to trigger AttributeError
        del starlette_user_mock.display_name

        proxy = StarletteUserProxy(starlette_user_mock)
        with pytest.raises(AttributeError, match='display_name'):
            _ = proxy.user_name


# --- JSONRPCApplication Tests (Selected) ---


class TestJSONRPCApplicationSetup:  # Renamed to avoid conflict
    def test_jsonrpc_app_build_method_abstract_raises_typeerror(
        self,
    ):  # Renamed test
        mock_handler = MagicMock(spec=RequestHandler)
        # Mock agent_card with essential attributes accessed in JSONRPCApplication.__init__
        mock_agent_card = MagicMock(spec=AgentCard)
        # Ensure 'url' attribute exists on the mock_agent_card, as it's accessed in __init__
        mock_agent_card.url = 'http://mockurl.com'
        # Ensure 'supportsAuthenticatedExtendedCard' attribute exists
        mock_agent_card.supports_authenticated_extended_card = False

        # This will fail at definition time if an abstract method is not implemented
        with pytest.raises(
            TypeError,
            match=".*abstract class IncompleteJSONRPCApp .* abstract method '?build'?",
        ):

            class IncompleteJSONRPCApp(JSONRPCApplication):
                # Intentionally not implementing 'build'
                def some_other_method(self):
                    pass

            IncompleteJSONRPCApp(
                agent_card=mock_agent_card, http_handler=mock_handler
            )


class TestJSONRPCApplicationOptionalDeps:
    # Running tests in this class requires optional dependencies starlette and
    # sse-starlette to be present in the test environment.

    @pytest.fixture(scope='class', autouse=True)
    def ensure_pkg_starlette_is_present(self):
        try:
            import sse_starlette as _sse_starlette  # noqa: F401
            import starlette as _starlette  # noqa: F401
        except ImportError:
            pytest.fail(
                f'Running tests in {self.__class__.__name__} requires'
                ' optional dependencies starlette and sse-starlette to be'
                ' present in the test environment. Run `uv sync --dev ...`'
                ' before running the test suite.'
            )

    @pytest.fixture(scope='class')
    def mock_app_params(self) -> dict:
        # Mock http_handler
        mock_handler = MagicMock(spec=RequestHandler)
        # Mock agent_card with essential attributes accessed in __init__
        mock_agent_card = MagicMock(spec=AgentCard)
        # Ensure 'url' attribute exists on the mock_agent_card, as it's accessed
        # in __init__
        mock_agent_card.url = 'http://example.com'
        # Ensure 'supportsAuthenticatedExtendedCard' attribute exists
        mock_agent_card.supports_authenticated_extended_card = False
        return {'agent_card': mock_agent_card, 'http_handler': mock_handler}

    @pytest.fixture(scope='class')
    def mark_pkg_starlette_not_installed(self):
        pkg_starlette_installed_flag = jsonrpc_app._package_starlette_installed
        jsonrpc_app._package_starlette_installed = False
        yield
        jsonrpc_app._package_starlette_installed = pkg_starlette_installed_flag

    def test_create_jsonrpc_based_app_with_present_deps_succeeds(
        self, mock_app_params: dict
    ):
        class DummyJSONRPCApp(JSONRPCApplication):
            def build(
                self,
                agent_card_url='/.well-known/agent.json',
                rpc_url='/',
                **kwargs,
            ):
                return object()

        try:
            _app = DummyJSONRPCApp(**mock_app_params)
        except ImportError:
            pytest.fail(
                'With packages starlette and see-starlette present, creating a'
                ' JSONRPCApplication-based instance should not raise'
                ' ImportError'
            )

    def test_create_jsonrpc_based_app_with_missing_deps_raises_importerror(
        self, mock_app_params: dict, mark_pkg_starlette_not_installed: Any
    ):
        class DummyJSONRPCApp(JSONRPCApplication):
            def build(
                self,
                agent_card_url='/.well-known/agent.json',
                rpc_url='/',
                **kwargs,
            ):
                return object()

        with pytest.raises(
            ImportError,
            match=(
                'Packages `starlette` and `sse-starlette` are required to use'
                ' the `JSONRPCApplication`'
            ),
        ):
            _app = DummyJSONRPCApp(**mock_app_params)


class TestJSONRPCExtensions:
    @pytest.fixture
    def mock_handler(self):
        handler = AsyncMock(spec=RequestHandler)
        handler.on_message_send.return_value = SendMessageResponse(
            root=SendMessageSuccessResponse(
                id='1',
                result=Message(
                    message_id='test',
                    role=Role.agent,
                    parts=[Part(TextPart(text='response message'))],
                ),
            )
        )
        return handler

    @pytest.fixture
    def test_app(self, mock_handler):
        mock_agent_card = MagicMock(spec=AgentCard)
        mock_agent_card.url = 'http://mockurl.com'
        mock_agent_card.supports_authenticated_extended_card = False

        return A2AStarletteApplication(
            agent_card=mock_agent_card, http_handler=mock_handler
        )

    @pytest.fixture
    def client(self, test_app):
        return TestClient(test_app.build())

    def test_request_with_single_extension(self, client, mock_handler):
        headers = {HTTP_EXTENSION_HEADER: 'foo'}
        response = client.post(
            '/',
            headers=headers,
            json=SendMessageRequest(
                id='1',
                params=MessageSendParams(
                    message=Message(
                        message_id='1',
                        role=Role.user,
                        parts=[Part(TextPart(text='hi'))],
                    )
                ),
            ).model_dump(),
        )
        response.raise_for_status()

        mock_handler.on_message_send.assert_called_once()
        call_context = mock_handler.on_message_send.call_args[0][1]
        assert isinstance(call_context, ServerCallContext)
        assert call_context.requested_extensions == {'foo'}

    def test_request_with_comma_separated_extensions(
        self, client, mock_handler
    ):
        headers = {HTTP_EXTENSION_HEADER: 'foo, bar'}
        response = client.post(
            '/',
            headers=headers,
            json=SendMessageRequest(
                id='1',
                params=MessageSendParams(
                    message=Message(
                        message_id='1',
                        role=Role.user,
                        parts=[Part(TextPart(text='hi'))],
                    )
                ),
            ).model_dump(),
        )
        response.raise_for_status()

        mock_handler.on_message_send.assert_called_once()
        call_context = mock_handler.on_message_send.call_args[0][1]
        assert call_context.requested_extensions == {'foo', 'bar'}

    def test_request_with_comma_separated_extensions_no_space(
        self, client, mock_handler
    ):
        headers = [
            (HTTP_EXTENSION_HEADER, 'foo,  bar'),
            (HTTP_EXTENSION_HEADER, 'baz'),
        ]
        response = client.post(
            '/',
            headers=headers,
            json=SendMessageRequest(
                id='1',
                params=MessageSendParams(
                    message=Message(
                        message_id='1',
                        role=Role.user,
                        parts=[Part(TextPart(text='hi'))],
                    )
                ),
            ).model_dump(),
        )
        response.raise_for_status()

        mock_handler.on_message_send.assert_called_once()
        call_context = mock_handler.on_message_send.call_args[0][1]
        assert call_context.requested_extensions == {'foo', 'bar', 'baz'}

    def test_method_added_to_call_context_state(self, client, mock_handler):
        response = client.post(
            '/',
            json=SendMessageRequest(
                id='1',
                params=MessageSendParams(
                    message=Message(
                        message_id='1',
                        role=Role.user,
                        parts=[Part(TextPart(text='hi'))],
                    )
                ),
            ).model_dump(),
        )
        response.raise_for_status()

        mock_handler.on_message_send.assert_called_once()
        call_context = mock_handler.on_message_send.call_args[0][1]
        assert call_context.state['method'] == 'message/send'

    def test_request_with_multiple_extension_headers(
        self, client, mock_handler
    ):
        headers = [
            (HTTP_EXTENSION_HEADER, 'foo'),
            (HTTP_EXTENSION_HEADER, 'bar'),
        ]
        response = client.post(
            '/',
            headers=headers,
            json=SendMessageRequest(
                id='1',
                params=MessageSendParams(
                    message=Message(
                        message_id='1',
                        role=Role.user,
                        parts=[Part(TextPart(text='hi'))],
                    )
                ),
            ).model_dump(),
        )
        response.raise_for_status()

        mock_handler.on_message_send.assert_called_once()
        call_context = mock_handler.on_message_send.call_args[0][1]
        assert call_context.requested_extensions == {'foo', 'bar'}

    def test_response_with_activated_extensions(self, client, mock_handler):
        def side_effect(request, context: ServerCallContext):
            context.activated_extensions.add('foo')
            context.activated_extensions.add('baz')
            return SendMessageResponse(
                root=SendMessageSuccessResponse(
                    id='1',
                    result=Message(
                        message_id='test',
                        role=Role.agent,
                        parts=[Part(TextPart(text='response message'))],
                    ),
                )
            )

        mock_handler.on_message_send.side_effect = side_effect

        response = client.post(
            '/',
            json=SendMessageRequest(
                id='1',
                params=MessageSendParams(
                    message=Message(
                        message_id='1',
                        role=Role.user,
                        parts=[Part(TextPart(text='hi'))],
                    )
                ),
            ).model_dump(),
        )
        response.raise_for_status()

        assert response.status_code == 200
        assert HTTP_EXTENSION_HEADER in response.headers
        assert set(response.headers[HTTP_EXTENSION_HEADER].split(', ')) == {
            'foo',
            'baz',
        }


if __name__ == '__main__':
    pytest.main([__file__])
