import json
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from starlette.requests import Request
from starlette.responses import JSONResponse

from a2a.server.context import ServerCallContext
from a2a.server.request_handlers.request_handler import RequestHandler
from a2a.server.routes import rest_dispatcher
from a2a.server.routes.rest_dispatcher import (
    RestDispatcher,
)
from a2a.types.a2a_pb2 import (
    AgentCapabilities,
    AgentCard,
    Message,
    SendMessageResponse,
    Task,
    TaskPushNotificationConfig,
    ListTasksResponse,
    ListTaskPushNotificationConfigsResponse,
)
from a2a.utils.errors import (
    ExtendedAgentCardNotConfiguredError,
    TaskNotFoundError,
    UnsupportedOperationError,
)


@pytest.fixture
def agent_card():
    card = MagicMock(spec=AgentCard)
    card.capabilities = AgentCapabilities(
        streaming=True,
        push_notifications=True,
        extended_agent_card=True,
    )
    return card


@pytest.fixture
def mock_handler(agent_card):
    handler = AsyncMock(spec=RequestHandler)
    # Default success cases
    handler._agent_card = agent_card
    handler.on_message_send.return_value = Message(message_id='test_msg')
    handler.on_cancel_task.return_value = Task(id='test_task')
    handler.on_get_task.return_value = Task(id='test_task')
    handler.on_get_extended_agent_card.return_value = agent_card()
    handler.on_list_tasks.return_value = ListTasksResponse()
    handler.on_get_task_push_notification_config.return_value = (
        TaskPushNotificationConfig(url='http://test')
    )
    handler.on_create_task_push_notification_config.return_value = (
        TaskPushNotificationConfig(url='http://test')
    )
    handler.on_list_task_push_notification_configs.return_value = (
        ListTaskPushNotificationConfigsResponse()
    )

    # Streaming mocks
    async def mock_stream(*args, **kwargs) -> AsyncIterator[Task]:
        yield Task(id='chunk1')
        yield Task(id='chunk2')

    handler.on_message_send_stream.side_effect = mock_stream
    handler.on_subscribe_to_task.side_effect = mock_stream
    return handler


@pytest.fixture
def rest_dispatcher_instance(mock_handler):
    return RestDispatcher(request_handler=mock_handler)


from starlette.datastructures import Headers


def make_mock_request(
    method: str = 'GET',
    path_params: dict | None = None,
    query_params: dict | None = None,
    headers: dict | None = None,
    body: bytes = b'{}',
) -> Request:
    mock_req = MagicMock(spec=Request)
    mock_req.method = method
    mock_req.path_params = path_params or {}
    mock_req.query_params = query_params or {}

    # Default valid headers for A2A
    default_headers = {'a2a-version': '1.0'}
    if headers:
        default_headers.update(headers)

    mock_req.headers = Headers(default_headers)
    mock_req.body = AsyncMock(return_value=body)

    # Needs to be able to build ServerCallContext, so provide .user and .auth etc. if needed
    mock_req.user = MagicMock(is_authenticated=False)
    mock_req.auth = None
    mock_req.scope = {}
    return mock_req


class TestRestDispatcherInitialization:
    @pytest.fixture(scope='class')
    def mark_pkg_starlette_not_installed(self):
        pkg_starlette_installed_flag = (
            rest_dispatcher._package_starlette_installed
        )
        rest_dispatcher._package_starlette_installed = False
        yield
        rest_dispatcher._package_starlette_installed = (
            pkg_starlette_installed_flag
        )

    def test_missing_starlette_raises_importerror(
        self, mark_pkg_starlette_not_installed, mock_handler
    ):
        with pytest.raises(
            ImportError,
            match='Packages `starlette` and `sse-starlette` are required',
        ):
            RestDispatcher(request_handler=mock_handler)


@pytest.mark.asyncio
class TestRestDispatcherContextManagement:
    async def test_build_call_context(self, rest_dispatcher_instance):
        req = make_mock_request(path_params={'tenant': 'my-tenant'})
        context = rest_dispatcher_instance._build_call_context(req)

        assert isinstance(context, ServerCallContext)
        assert context.tenant == 'my-tenant'
        assert context.state['headers']['a2a-version'] == '1.0'


@pytest.mark.asyncio
class TestRestDispatcherEndpoints:
    async def test_on_message_send_throws_error_for_unsupported_version(
        self, rest_dispatcher_instance, mock_handler
    ):
        # 0.3 is currently not supported for direct message sending on RestDispatcher
        req = make_mock_request(method='POST', headers={'a2a-version': '0.3.0'})
        response = await rest_dispatcher_instance.on_message_send(req)

        # VersionNotSupportedError maps to 400 Bad Request
        assert response.status_code == 400

    async def test_on_message_send_returns_message(
        self, rest_dispatcher_instance, mock_handler
    ):
        req = make_mock_request(method='POST')
        response = await rest_dispatcher_instance.on_message_send(req)

        assert isinstance(response, JSONResponse)
        assert response.status_code == 200
        data = json.loads(response.body)
        assert 'message' in data

    async def test_on_message_send_returns_task(
        self, rest_dispatcher_instance, mock_handler
    ):
        mock_handler.on_message_send.return_value = Task(id='new_task')
        req = make_mock_request(method='POST')

        response = await rest_dispatcher_instance.on_message_send(req)
        assert response.status_code == 200
        data = json.loads(response.body)
        assert 'task' in data
        assert data['task']['id'] == 'new_task'

    async def test_on_cancel_task_success(
        self, rest_dispatcher_instance, mock_handler
    ):
        req = make_mock_request(method='POST', path_params={'id': 'test_task'})
        response = await rest_dispatcher_instance.on_cancel_task(req)

        assert response.status_code == 200
        data = json.loads(response.body)
        assert data['id'] == 'test_task'

    async def test_on_cancel_task_not_found(
        self, rest_dispatcher_instance, mock_handler
    ):
        mock_handler.on_cancel_task.return_value = None
        req = make_mock_request(method='POST', path_params={'id': 'test_task'})

        response = await rest_dispatcher_instance.on_cancel_task(req)
        assert response.status_code == 404  # TaskNotFoundError maps to 404

    async def test_on_get_task_success(
        self, rest_dispatcher_instance, mock_handler
    ):
        req = make_mock_request(method='GET', path_params={'id': 'test_task'})
        response = await rest_dispatcher_instance.on_get_task(req)

        assert response.status_code == 200
        data = json.loads(response.body)
        assert data['id'] == 'test_task'

    async def test_on_get_task_not_found(
        self, rest_dispatcher_instance, mock_handler
    ):
        mock_handler.on_get_task.return_value = None
        req = make_mock_request(
            method='GET', path_params={'id': 'missing_task'}
        )

        response = await rest_dispatcher_instance.on_get_task(req)
        assert response.status_code == 404

    async def test_list_tasks(self, rest_dispatcher_instance, mock_handler):
        req = make_mock_request(method='GET')
        response = await rest_dispatcher_instance.list_tasks(req)
        assert response.status_code == 200

    async def test_get_push_notification(
        self, rest_dispatcher_instance, mock_handler
    ):
        req = make_mock_request(
            method='GET', path_params={'id': 'task1', 'push_id': 'push1'}
        )
        response = await rest_dispatcher_instance.get_push_notification(req)
        assert response.status_code == 200
        data = json.loads(response.body)
        assert data['url'] == 'http://test'

    async def test_delete_push_notification(
        self, rest_dispatcher_instance, mock_handler
    ):
        req = make_mock_request(
            method='DELETE', path_params={'id': 'task1', 'push_id': 'push1'}
        )
        response = await rest_dispatcher_instance.delete_push_notification(req)
        assert response.status_code == 200

    async def test_handle_authenticated_agent_card(
        self, rest_dispatcher_instance
    ):
        req = make_mock_request()
        response = (
            await rest_dispatcher_instance.handle_authenticated_agent_card(req)
        )
        assert response.status_code == 200


@pytest.mark.asyncio
class TestRestDispatcherStreaming:
    async def test_on_message_send_stream_success(
        self, rest_dispatcher_instance
    ):
        req = make_mock_request(method='POST')
        response = await rest_dispatcher_instance.on_message_send_stream(req)

        assert response.status_code == 200

        chunks = []
        async for chunk in response.body_iterator:
            chunks.append(chunk)

        assert len(chunks) == 2
        assert 'chunk1' in chunks[0].data
        assert 'chunk2' in chunks[1].data

    async def test_on_subscribe_to_task_success(self, rest_dispatcher_instance):
        req = make_mock_request(method='GET', path_params={'id': 'test_task'})
        response = await rest_dispatcher_instance.on_subscribe_to_task(req)

        assert response.status_code == 200

        chunks = []
        async for chunk in response.body_iterator:
            chunks.append(chunk)

        assert len(chunks) == 2
        assert 'chunk1' in chunks[0].data
        assert 'chunk2' in chunks[1].data

    async def test_on_message_send_stream_handler_error(self, mock_handler):
        from a2a.utils.errors import UnsupportedOperationError

        mock_handler.on_message_send_stream.side_effect = (
            UnsupportedOperationError('Mocked error')
        )

        dispatcher = RestDispatcher(request_handler=mock_handler)
        req = make_mock_request(method='POST')

        response = await dispatcher.on_message_send_stream(req)
        assert response.status_code == 400
