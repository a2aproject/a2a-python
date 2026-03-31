import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.responses import JSONResponse
from starlette.testclient import TestClient

try:
    from starlette.authentication import BaseUser as StarletteBaseUser
except ImportError:
    StarletteBaseUser = MagicMock()  # type: ignore

from a2a.extensions.common import HTTP_EXTENSION_HEADER
from a2a.server.context import ServerCallContext
from a2a.server.request_handlers.request_handler import RequestHandler
from a2a.types.a2a_pb2 import (
    AgentCard,
    Message,
    Part,
    Role,
)
from a2a.server.routes import jsonrpc_dispatcher
from a2a.server.routes.common import (
    CallContextBuilder,
    DefaultCallContextBuilder,
    StarletteUserProxy,
)
from a2a.server.routes.jsonrpc_dispatcher import JsonRpcDispatcher
from a2a.server.routes.jsonrpc_routes import create_jsonrpc_routes
from a2a.server.routes.agent_card_routes import create_agent_card_routes
from a2a.server.jsonrpc_models import JSONRPCError
from a2a.utils.errors import A2AError


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
        starlette_user_mock = MagicMock(spec=StarletteBaseUser)
        del starlette_user_mock.display_name

        proxy = StarletteUserProxy(starlette_user_mock)
        with pytest.raises(AttributeError, match='display_name'):
            _ = proxy.user_name


# --- JsonRpcDispatcher Tests ---


@pytest.fixture
def mock_handler():
    handler = AsyncMock(spec=RequestHandler)
    handler.on_message_send.return_value = Message(
        message_id='test',
        role=Role.ROLE_AGENT,
        parts=[Part(text='response message')],
    )
    return handler


@pytest.fixture
def test_app(mock_handler):
    mock_agent_card = MagicMock(spec=AgentCard)
    mock_agent_card.url = 'http://mockurl.com'
    mock_agent_card.capabilities = MagicMock()
    mock_agent_card.capabilities.streaming = False

    jsonrpc_routes = create_jsonrpc_routes(
        agent_card=mock_agent_card, request_handler=mock_handler, rpc_url='/'
    )

    from starlette.applications import Starlette

    return Starlette(routes=jsonrpc_routes)


@pytest.fixture
def client(test_app):
    return TestClient(test_app, headers={'A2A-Version': '1.0'})


def _make_send_message_request(
    text: str = 'hi', tenant: str | None = None
) -> dict:
    params: dict[str, Any] = {
        'message': {
            'messageId': '1',
            'role': 'ROLE_USER',
            'parts': [{'text': text}],
        }
    }
    if tenant is not None:
        params['tenant'] = tenant

    return {
        'jsonrpc': '2.0',
        'id': '1',
        'method': 'SendMessage',
        'params': params,
    }


class TestJsonRpcDispatcherOptionalDependencies:
    @pytest.fixture(scope='class')
    def mock_app_params(self) -> dict:
        mock_handler = MagicMock(spec=RequestHandler)
        mock_agent_card = MagicMock(spec=AgentCard)
        mock_agent_card.url = 'http://example.com'
        return {'agent_card': mock_agent_card, 'request_handler': mock_handler}

    @pytest.fixture(scope='class')
    def mark_pkg_starlette_not_installed(self):
        pkg_starlette_installed_flag = (
            jsonrpc_dispatcher._package_starlette_installed
        )
        jsonrpc_dispatcher._package_starlette_installed = False
        yield
        jsonrpc_dispatcher._package_starlette_installed = (
            pkg_starlette_installed_flag
        )

    def test_create_dispatcher_with_missing_deps_raises_importerror(
        self, mock_app_params: dict, mark_pkg_starlette_not_installed: Any
    ):
        with pytest.raises(
            ImportError,
            match=(
                'Packages `starlette` and `sse-starlette` are required to use'
                ' the `JsonRpcDispatcher`'
            ),
        ):
            JsonRpcDispatcher(**mock_app_params)


class TestJsonRpcDispatcherExtensions:
    def test_request_with_single_extension(self, client, mock_handler):
        headers = {HTTP_EXTENSION_HEADER: 'foo'}
        response = client.post(
            '/',
            headers=headers,
            json=_make_send_message_request(),
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
            json=_make_send_message_request(),
        )
        response.raise_for_status()

        mock_handler.on_message_send.assert_called_once()
        call_context = mock_handler.on_message_send.call_args[0][1]
        assert call_context.requested_extensions == {'foo', 'bar'}

    def test_method_added_to_call_context_state(self, client, mock_handler):
        response = client.post(
            '/',
            json=_make_send_message_request(),
        )
        response.raise_for_status()

        mock_handler.on_message_send.assert_called_once()
        call_context = mock_handler.on_message_send.call_args[0][1]
        assert call_context.state['method'] == 'SendMessage'

    def test_response_with_activated_extensions(self, client, mock_handler):
        def side_effect(request, context: ServerCallContext):
            context.activated_extensions.add('foo')
            context.activated_extensions.add('baz')
            return Message(
                message_id='test',
                role=Role.ROLE_AGENT,
                parts=[Part(text='response message')],
            )

        mock_handler.on_message_send.side_effect = side_effect

        response = client.post(
            '/',
            json=_make_send_message_request(),
        )
        response.raise_for_status()

        assert response.status_code == 200
        assert HTTP_EXTENSION_HEADER in response.headers
        assert set(response.headers[HTTP_EXTENSION_HEADER].split(', ')) == {
            'foo',
            'baz',
        }


class TestJsonRpcDispatcherTenant:
    def test_tenant_extraction_from_params(self, client, mock_handler):
        tenant_id = 'my-tenant-123'
        response = client.post(
            '/',
            json=_make_send_message_request(tenant=tenant_id),
        )
        response.raise_for_status()

        mock_handler.on_message_send.assert_called_once()
        call_context = mock_handler.on_message_send.call_args[0][1]
        assert isinstance(call_context, ServerCallContext)
        assert call_context.tenant == tenant_id

    def test_no_tenant_extraction(self, client, mock_handler):
        response = client.post(
            '/',
            json=_make_send_message_request(tenant=None),
        )
        response.raise_for_status()

        mock_handler.on_message_send.assert_called_once()
        call_context = mock_handler.on_message_send.call_args[0][1]
        assert isinstance(call_context, ServerCallContext)
        assert call_context.tenant == ''


class TestJsonRpcDispatcherV03Compat:
    def test_v0_3_compat_flag_routes_to_adapter(self, mock_handler):
        mock_agent_card = MagicMock(spec=AgentCard)
        mock_agent_card.url = 'http://mockurl.com'
        mock_agent_card.capabilities = MagicMock()
        mock_agent_card.capabilities.streaming = False

        from starlette.applications import Starlette

        jsonrpc_routes = create_jsonrpc_routes(
            agent_card=mock_agent_card,
            request_handler=mock_handler,
            enable_v0_3_compat=True,
            rpc_url='/',
        )
        app = Starlette(routes=jsonrpc_routes)
        client = TestClient(app)

        request_data = {
            'jsonrpc': '2.0',
            'id': '1',
            'method': 'message/send',
            'params': {
                'message': {
                    'messageId': 'msg-1',
                    'role': 'ROLE_USER',
                    'parts': [{'text': 'Hello'}],
                }
            },
        }

        dispatcher_instance = jsonrpc_routes[0].endpoint.__self__
        with patch.object(
            dispatcher_instance._v03_adapter,
            'handle_request',
            new_callable=AsyncMock,
        ) as mock_handle:
            mock_handle.return_value = JSONResponse(
                {'jsonrpc': '2.0', 'id': '1', 'result': {}}
            )

            response = client.post('/', json=request_data)

            response.raise_for_status()
            assert mock_handle.called
            assert mock_handle.call_args[1]['method'] == 'message/send'


if __name__ == '__main__':
    pytest.main([__file__])
