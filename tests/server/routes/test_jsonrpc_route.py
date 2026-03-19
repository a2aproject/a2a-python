from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from starlette.responses import JSONResponse
from starlette.testclient import TestClient

from a2a.extensions.common import HTTP_EXTENSION_HEADER
from a2a.server.routes import JsonRpcRoute, StarletteUserProxy

from a2a.server.context import ServerCallContext
from a2a.server.request_handlers.request_handler import RequestHandler
from a2a.types.a2a_pb2 import (
    AgentCard,
    Message,
    Part,
    Role,
)


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

    from starlette.applications import Starlette

    app = Starlette()
    router = JsonRpcRoute(mock_agent_card, mock_handler)
    app.routes.append(router.route)
    return app


@pytest.fixture
def client(test_app):
    return TestClient(test_app)


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


class TestJSONRPCApplicationExtensions:
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
            json=_make_send_message_request(),
        )
        response.raise_for_status()

        mock_handler.on_message_send.assert_called_once()
        call_context = mock_handler.on_message_send.call_args[0][1]
        assert call_context.requested_extensions == {'foo', 'bar', 'baz'}

    def test_method_added_to_call_context_state(self, client, mock_handler):
        response = client.post(
            '/',
            json=_make_send_message_request(),
        )
        response.raise_for_status()

        mock_handler.on_message_send.assert_called_once()
        call_context = mock_handler.on_message_send.call_args[0][1]
        assert call_context.state['method'] == 'SendMessage'

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
            json=_make_send_message_request(),
        )
        response.raise_for_status()

        mock_handler.on_message_send.assert_called_once()
        call_context = mock_handler.on_message_send.call_args[0][1]
        assert call_context.requested_extensions == {'foo', 'bar'}

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


class TestJSONRPCApplicationTenant:
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


class TestJSONRPCApplicationV03Compat:
    def test_v0_3_compat_flag_routes_to_dispatcher(self, mock_handler):
        mock_agent_card = MagicMock(spec=AgentCard)
        mock_agent_card.url = 'http://mockurl.com'
        mock_agent_card.capabilities = MagicMock()
        mock_agent_card.capabilities.streaming = False

        from starlette.applications import Starlette

        app = Starlette()
        router = JsonRpcRoute(
            mock_agent_card, mock_handler, enable_v0_3_compat=True
        )
        app.routes.append(router.route)

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

        # Instead of _v03_adapter, the handler handles it or it's dispatcher
        with patch.object(
            router.dispatcher,
            '_process_non_streaming_request',
            new_callable=AsyncMock,
        ) as mock_handle:
            mock_handle.return_value = {
                'jsonrpc': '2.0',
                'id': '1',
                'result': {},
            }

            response = client.post('/', json=request_data)

            response.raise_for_status()
            assert mock_handle.called

    def test_v0_3_compat_flag_disabled_rejects_v0_3_method(self, mock_handler):
        mock_agent_card = MagicMock(spec=AgentCard)
        mock_agent_card.url = 'http://mockurl.com'
        mock_agent_card.capabilities = MagicMock()
        mock_agent_card.capabilities.streaming = False

        from starlette.applications import Starlette

        app = Starlette()
        router = JsonRpcRoute(
            mock_agent_card, mock_handler, enable_v0_3_compat=False
        )
        app.routes.append(router.route)

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

        response = client.post('/', json=request_data)

        assert response.status_code == 200
        resp_json = response.json()
        assert 'error' in resp_json
        assert resp_json['error']['code'] == -32601


if __name__ == '__main__':
    pytest.main([__file__])
