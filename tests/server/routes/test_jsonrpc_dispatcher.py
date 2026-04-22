import asyncio
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
    AgentCapabilities,
    AgentCard,
    Artifact,
    ListTaskPushNotificationConfigsResponse,
    ListTasksResponse,
    Message,
    Part,
    Role,
    Task,
    TaskArtifactUpdateEvent,
    TaskPushNotificationConfig,
    TaskState,
    TaskStatus,
)
from a2a.server.routes import jsonrpc_dispatcher

from a2a.server.routes.jsonrpc_dispatcher import JsonRpcDispatcher
from a2a.server.routes.jsonrpc_routes import create_jsonrpc_routes
from a2a.server.routes.agent_card_routes import create_agent_card_routes
from a2a.server.jsonrpc_models import JSONRPCError
from a2a.utils.errors import A2AError


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
        request_handler=mock_handler, rpc_url='/'
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
        mock_handler._agent_card = mock_agent_card
        return {'request_handler': mock_handler}

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

        mock_handler._agent_card = mock_agent_card

        from starlette.applications import Starlette

        jsonrpc_routes = create_jsonrpc_routes(
            request_handler=mock_handler, enable_v0_3_compat=True, rpc_url='/'
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


def _make_jsonrpc_request(method: str, params: dict | None = None) -> dict:
    """Helper to build a JSON-RPC 2.0 request dict."""
    return {
        'jsonrpc': '2.0',
        'id': '1',
        'method': method,
        'params': params or {},
    }


class TestJsonRpcDispatcherMethodRouting:
    """Tests that each JSON-RPC method name routes to the correct handler."""

    @pytest.fixture
    def handler(self):
        handler = AsyncMock(spec=RequestHandler)
        handler.on_message_send.return_value = Message(
            message_id='test',
            role=Role.ROLE_AGENT,
            parts=[Part(text='ok')],
        )
        handler.on_cancel_task.return_value = Task(
            id='task1',
            context_id='ctx1',
            status=TaskStatus(state=TaskState.TASK_STATE_CANCELED),
        )
        handler.on_get_task.return_value = Task(
            id='task1',
            context_id='ctx1',
            status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
        )
        handler.on_list_tasks.return_value = ListTasksResponse()
        handler.on_create_task_push_notification_config.return_value = (
            TaskPushNotificationConfig(task_id='t1', url='https://example.com')
        )
        handler.on_get_task_push_notification_config.return_value = (
            TaskPushNotificationConfig(task_id='t1', url='https://example.com')
        )
        handler.on_list_task_push_notification_configs.return_value = (
            ListTaskPushNotificationConfigsResponse()
        )
        handler.on_delete_task_push_notification_config.return_value = None
        return handler

    @pytest.fixture
    def agent_card(self):
        return AgentCard(
            capabilities=AgentCapabilities(
                streaming=True,
                push_notifications=True,
                extended_agent_card=True,
            ),
            name='TestAgent',
            version='1.0',
        )

    @pytest.fixture
    def client(self, handler, agent_card):
        jsonrpc_routes = create_jsonrpc_routes(
            request_handler=handler,
            rpc_url='/',
        )
        from starlette.applications import Starlette

        app = Starlette(routes=jsonrpc_routes)
        return TestClient(app, headers={'A2A-Version': '1.0'})

    # --- Non-streaming method routing tests ---

    def test_send_message_routes_to_on_message_send(self, client, handler):
        response = client.post(
            '/',
            json=_make_jsonrpc_request(
                'SendMessage',
                {
                    'message': {
                        'messageId': '1',
                        'role': 'ROLE_USER',
                        'parts': [{'text': 'hello'}],
                    }
                },
            ),
        )
        response.raise_for_status()

        handler.on_message_send.assert_called_once()
        call_context = handler.on_message_send.call_args[0][1]
        assert call_context.state['method'] == 'SendMessage'

    def test_cancel_task_routes_to_on_cancel_task(self, client, handler):
        response = client.post(
            '/',
            json=_make_jsonrpc_request('CancelTask', {'id': 'task1'}),
        )
        response.raise_for_status()

        handler.on_cancel_task.assert_called_once()
        call_context = handler.on_cancel_task.call_args[0][1]
        assert call_context.state['method'] == 'CancelTask'

    def test_get_task_routes_to_on_get_task(self, client, handler):
        response = client.post(
            '/',
            json=_make_jsonrpc_request('GetTask', {'id': 'task1'}),
        )
        response.raise_for_status()

        handler.on_get_task.assert_called_once()
        call_context = handler.on_get_task.call_args[0][1]
        assert call_context.state['method'] == 'GetTask'

    def test_list_tasks_routes_to_on_list_tasks(self, client, handler):
        response = client.post(
            '/',
            json=_make_jsonrpc_request('ListTasks'),
        )
        response.raise_for_status()

        handler.on_list_tasks.assert_called_once()
        call_context = handler.on_list_tasks.call_args[0][1]
        assert call_context.state['method'] == 'ListTasks'

    def test_create_push_notification_config_routes_correctly(
        self, client, handler
    ):
        response = client.post(
            '/',
            json=_make_jsonrpc_request(
                'CreateTaskPushNotificationConfig',
                {'taskId': 't1', 'url': 'https://example.com'},
            ),
        )
        response.raise_for_status()

        handler.on_create_task_push_notification_config.assert_called_once()
        call_context = (
            handler.on_create_task_push_notification_config.call_args[0][1]
        )
        assert (
            call_context.state['method'] == 'CreateTaskPushNotificationConfig'
        )

    def test_get_push_notification_config_routes_correctly(
        self, client, handler
    ):
        response = client.post(
            '/',
            json=_make_jsonrpc_request(
                'GetTaskPushNotificationConfig',
                {'taskId': 't1', 'id': 'config1'},
            ),
        )
        response.raise_for_status()

        handler.on_get_task_push_notification_config.assert_called_once()
        call_context = handler.on_get_task_push_notification_config.call_args[
            0
        ][1]
        assert call_context.state['method'] == 'GetTaskPushNotificationConfig'

    def test_list_push_notification_configs_routes_correctly(
        self, client, handler
    ):
        response = client.post(
            '/',
            json=_make_jsonrpc_request(
                'ListTaskPushNotificationConfigs',
                {'taskId': 't1'},
            ),
        )
        response.raise_for_status()

        handler.on_list_task_push_notification_configs.assert_called_once()
        call_context = handler.on_list_task_push_notification_configs.call_args[
            0
        ][1]
        assert call_context.state['method'] == 'ListTaskPushNotificationConfigs'

    def test_delete_push_notification_config_routes_correctly(
        self, client, handler
    ):
        response = client.post(
            '/',
            json=_make_jsonrpc_request(
                'DeleteTaskPushNotificationConfig',
                {'taskId': 't1', 'id': 'config1'},
            ),
        )
        response.raise_for_status()
        data = response.json()
        assert data.get('result') is None

        handler.on_delete_task_push_notification_config.assert_called_once()
        call_context = (
            handler.on_delete_task_push_notification_config.call_args[0][1]
        )
        assert (
            call_context.state['method'] == 'DeleteTaskPushNotificationConfig'
        )

    def test_get_extended_agent_card_routes_correctly(
        self, handler, agent_card
    ):
        captured: dict[str, Any] = {}

        async def capture_modifier(card, context):
            captured['method'] = context.state.get('method')
            return card

        handler.on_get_extended_agent_card.return_value = agent_card
        jsonrpc_routes = create_jsonrpc_routes(
            request_handler=handler,
            rpc_url='/',
        )
        from starlette.applications import Starlette

        app = Starlette(routes=jsonrpc_routes)
        client = TestClient(app, headers={'A2A-Version': '1.0'})

        response = client.post(
            '/',
            json=_make_jsonrpc_request('GetExtendedAgentCard'),
        )
        response.raise_for_status()
        data = response.json()
        assert 'result' in data
        assert data['result']['name'] == 'TestAgent'
        handler.on_get_extended_agent_card.assert_called_once()

    # --- Streaming method routing tests ---

    @pytest.mark.asyncio
    async def test_send_streaming_message_routes_to_on_message_send_stream(
        self, handler, agent_card
    ):
        async def stream_generator():
            yield TaskArtifactUpdateEvent(
                artifact=Artifact(
                    artifact_id='a1',
                    name='result',
                    parts=[Part(text='streamed')],
                ),
                task_id='task1',
                context_id='ctx1',
                append=False,
                last_chunk=True,
            )

        handler.on_message_send_stream = MagicMock(
            return_value=stream_generator()
        )

        jsonrpc_routes = create_jsonrpc_routes(
            request_handler=handler,
            rpc_url='/',
        )
        from starlette.applications import Starlette

        app = Starlette(routes=jsonrpc_routes)
        client = TestClient(app, headers={'A2A-Version': '1.0'})

        try:
            with client.stream(
                'POST',
                '/',
                json=_make_jsonrpc_request(
                    'SendStreamingMessage',
                    {
                        'message': {
                            'messageId': '1',
                            'role': 'ROLE_USER',
                            'parts': [{'text': 'hello'}],
                        }
                    },
                ),
            ) as response:
                assert response.status_code == 200
                assert response.headers['content-type'].startswith(
                    'text/event-stream'
                )
                content = b''
                for chunk in response.iter_bytes():
                    content += chunk
                assert b'a1' in content
        finally:
            client.close()
            await asyncio.sleep(0.1)

        handler.on_message_send_stream.assert_called_once()
        call_context = handler.on_message_send_stream.call_args[0][1]
        assert call_context.state['method'] == 'SendStreamingMessage'

    @pytest.mark.asyncio
    async def test_subscribe_to_task_routes_to_on_subscribe_to_task(
        self, handler, agent_card
    ):
        async def stream_generator():
            yield TaskArtifactUpdateEvent(
                artifact=Artifact(
                    artifact_id='a1',
                    name='result',
                    parts=[Part(text='streamed')],
                ),
                task_id='task1',
                context_id='ctx1',
                append=False,
                last_chunk=True,
            )

        handler.on_subscribe_to_task = MagicMock(
            return_value=stream_generator()
        )

        jsonrpc_routes = create_jsonrpc_routes(
            request_handler=handler,
            rpc_url='/',
        )
        from starlette.applications import Starlette

        app = Starlette(routes=jsonrpc_routes)
        client = TestClient(app, headers={'A2A-Version': '1.0'})

        try:
            with client.stream(
                'POST',
                '/',
                json=_make_jsonrpc_request(
                    'SubscribeToTask',
                    {
                        'id': 'task1',
                    },
                ),
            ) as response:
                assert response.status_code == 200
                assert response.headers['content-type'].startswith(
                    'text/event-stream'
                )
                content = b''
                for chunk in response.iter_bytes():
                    content += chunk
                assert b'a1' in content
        finally:
            client.close()
            await asyncio.sleep(0.1)

        handler.on_subscribe_to_task.assert_called_once()
        call_context = handler.on_subscribe_to_task.call_args[0][1]
        assert call_context.state['method'] == 'SubscribeToTask'


if __name__ == '__main__':
    pytest.main([__file__])
