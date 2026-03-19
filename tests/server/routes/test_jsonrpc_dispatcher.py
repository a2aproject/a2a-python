import pytest
import json
from unittest.mock import AsyncMock, MagicMock, patch
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.datastructures import Headers

from a2a.server.routes.jsonrpc_dispatcher import JsonRpcDispatcher
from a2a.server.request_handlers.request_handler import RequestHandler
from a2a.types.a2a_pb2 import (
    AgentCard, Message, Role, Part, GetTaskRequest, Task, 
    ListTasksResponse, TaskPushNotificationConfig, 
    ListTaskPushNotificationConfigsResponse
)
from a2a.server.jsonrpc_models import (
    JSONParseError,
    InvalidRequestError,
    MethodNotFoundError,
    InvalidParamsError,
)

@pytest.fixture
def mock_handler():
    handler = AsyncMock(spec=RequestHandler)
    return handler

@pytest.fixture
def agent_card():
    card = MagicMock(spec=AgentCard)
    card.capabilities = MagicMock()
    card.capabilities.streaming = True
    card.capabilities.push_notifications = True
    return card

class TestJsonRpcDispatcher:
    def _create_request(self, body_dict=None, headers=None, body_bytes=None):
        """Helper to create a starlette Request for testing"""
        scope = {
            'type': 'http',
            'method': 'POST',
            'path': '/',
            'headers': Headers(headers or {}).raw
        }
        
        async def receive():
            if body_bytes:
                 return {'type': 'http.request', 'body': body_bytes, 'more_body': False}
            return {'type': 'http.request', 'body': json.dumps(body_dict or {}).encode('utf-8'), 'more_body': False}

        return Request(scope, receive)

    @pytest.mark.asyncio
    async def test_generate_error_response(self, agent_card, mock_handler):
        dispatcher = JsonRpcDispatcher(agent_card, mock_handler)
        resp = dispatcher._generate_error_response(1, JSONParseError(message='test'))
        assert resp.status_code == 200
        data = json.loads(resp.body.decode())
        assert data['id'] == 1
        assert 'error' in data
        assert data['error']['code'] == -32700

    @pytest.mark.asyncio
    @pytest.mark.parametrize("method, params, handler_attr, mock_return, expected_key", [
        ("GetTask", {"id": "task-1"}, "on_get_task", Task(id="task-1"), "id"),
        ("SendMessage", {"message": {"parts": [{"text": "hi"}]}}, "on_message_send", Task(id="task-1"), "task"),
        ("CancelTask", {"id": "task-1"}, "on_cancel_task", Task(id="task-1"), "id"),
        ("ListTasks", {}, "on_list_tasks", ListTasksResponse(tasks=[Task(id="task-1")]), "tasks"),
        ("CreateTaskPushNotificationConfig", {"taskId": "task-1"}, "on_create_task_push_notification_config", TaskPushNotificationConfig(task_id="task-1"), "taskId"),
        ("GetTaskPushNotificationConfig", {"taskId": "task-1"}, "on_get_task_push_notification_config", TaskPushNotificationConfig(task_id="task-1"), "taskId"),
        ("ListTaskPushNotificationConfigs", {}, "on_list_task_push_notification_configs", ListTaskPushNotificationConfigsResponse(configs=[TaskPushNotificationConfig(task_id="task-1")]), "configs"),
        ("DeleteTaskPushNotificationConfig", {"taskId": "task-1"}, "on_delete_task_push_notification_config", None, None),
    ])
    async def test_handle_requests_success_non_streaming(self, agent_card, mock_handler, method, params, handler_attr, mock_return, expected_key):
        dispatcher = JsonRpcDispatcher(agent_card, mock_handler)
        req_body = {
            'jsonrpc': '2.0',
            'id': 'msg-1',
            'method': method,
            'params': params
        }
        req = self._create_request(body_dict=req_body)
        
        mock_func = getattr(mock_handler, handler_attr)
        if hasattr(mock_func, 'return_value'):
            mock_func.return_value = mock_return

        resp = await dispatcher.handle_requests(req)
        assert resp.status_code == 200
        res = json.loads(resp.body.decode())
        assert res['id'] == 'msg-1'
        if expected_key:
            assert 'result' in res
            assert expected_key in res['result']
        assert mock_func.called

    @pytest.mark.asyncio
    async def test_handle_requests_payload_too_large(self, agent_card, mock_handler):
        dispatcher = JsonRpcDispatcher(agent_card, mock_handler, max_content_length=10)
        req = self._create_request(
             body_dict={'jsonrpc': '2.0', 'id': '1', 'method': 'GetTask'},
             headers={'content-length': '100'}
        )
        resp = await dispatcher.handle_requests(req)
        res = json.loads(resp.body.decode())
        assert res['error']['code'] == -32600
        assert 'Payload too large' in res['error']['message']

    @pytest.mark.asyncio
    async def test_handle_requests_batch_not_supported(self, agent_card, mock_handler):
        dispatcher = JsonRpcDispatcher(agent_card, mock_handler)
        req = self._create_request(body_dict=[{'jsonrpc': '2.0'}])
        resp = await dispatcher.handle_requests(req)
        res = json.loads(resp.body.decode())
        assert res['error']['code'] == -32600
        # The underlying jsonrpc library formats the exact text differently depending on parse path
        assert 'Invalid Request' in res['error']['message']

    @pytest.mark.asyncio
    async def test_handle_requests_invalid_jsonrpc_version(self, agent_card, mock_handler):
        dispatcher = JsonRpcDispatcher(agent_card, mock_handler)
        req = self._create_request(body_dict={'jsonrpc': '1.0', 'id': '1', 'method': 'GetTask'})
        resp = await dispatcher.handle_requests(req)
        res = json.loads(resp.body.decode())
        assert res['error']['code'] == -32600

    @pytest.mark.asyncio
    async def test_handle_requests_method_not_found(self, agent_card, mock_handler):
        dispatcher = JsonRpcDispatcher(agent_card, mock_handler)
        req = self._create_request(body_dict={'jsonrpc': '2.0', 'id': '1', 'method': 'UnknownMethod'})
        resp = await dispatcher.handle_requests(req)
        res = json.loads(resp.body.decode())
        assert res['error']['code'] == -32601

    @pytest.mark.asyncio
    async def test_v03_compat_delegation(self, agent_card, mock_handler):
        dispatcher = JsonRpcDispatcher(agent_card, mock_handler, enable_v0_3_compat=True)
        dispatcher._v03_adapter.supports_method = MagicMock(return_value=True)
        dispatcher._v03_adapter.handle_request = AsyncMock(return_value=JSONResponse({'v03': 'compat'}))

        req = self._create_request(body_dict={'jsonrpc': '2.0', 'id': '1', 'method': 'message/send'})
        resp = await dispatcher.handle_requests(req)
        res = json.loads(resp.body.decode())
        assert res == {'v03': 'compat'}

    @pytest.mark.asyncio
    async def test_invalid_json_body_error(self, agent_card, mock_handler):
        dispatcher = JsonRpcDispatcher(agent_card, mock_handler)
        req = self._create_request(body_bytes=b'{"invalid": json}')
        
        resp = await dispatcher.handle_requests(req)
        res = json.loads(resp.body.decode())
        assert res['error']['code'] == -32700
