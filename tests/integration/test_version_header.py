import pytest

from fastapi import FastAPI
from starlette.testclient import TestClient

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.routes.rest_routes import create_rest_routes
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.events import EventQueue
from a2a.server.events.in_memory_queue_manager import InMemoryQueueManager
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks.inmemory_push_notification_config_store import (
    InMemoryPushNotificationConfigStore,
)
from a2a.server.tasks.inmemory_task_store import InMemoryTaskStore
from a2a.types.a2a_pb2 import AgentCapabilities, AgentCard, Task
from a2a.utils.constants import VERSION_HEADER


class DummyAgentExecutor(AgentExecutor):
    async def execute(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        pass

    async def cancel(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        pass


@pytest.fixture
def test_app():
    agent_card = AgentCard(
        name='Test Agent',
        version='1.0.0',
        capabilities=AgentCapabilities(streaming=True),
    )
    handler = DefaultRequestHandler(
        agent_executor=DummyAgentExecutor(),
        task_store=InMemoryTaskStore(),
        agent_card=agent_card,
        queue_manager=InMemoryQueueManager(),
        push_config_store=InMemoryPushNotificationConfigStore(),
    )

    async def mock_on_message_send(*args, **kwargs):
        task = Task(id='task-123')
        task.status.message.message_id = 'msg-123'
        return task

    async def mock_on_message_send_stream(*args, **kwargs):
        task = Task(id='task-123')
        task.status.message.message_id = 'msg-123'
        yield task

    handler.on_message_send = mock_on_message_send
    handler.on_message_send_stream = mock_on_message_send_stream

    app = FastAPI()
    agent_card_routes = create_agent_card_routes(
        agent_card=agent_card, card_url='/'
    )
    jsonrpc_routes = create_jsonrpc_routes(
        request_handler=handler, rpc_url='/jsonrpc', enable_v0_3_compat=True
    )
    app.routes.extend(agent_card_routes)
    app.routes.extend(jsonrpc_routes)

    rest_routes = create_rest_routes(
        request_handler=handler, path_prefix='/rest', enable_v0_3_compat=True
    )
    app.routes.extend(rest_routes)
    return app


@pytest.fixture
def client(test_app):
    return TestClient(test_app, raise_server_exceptions=False)


@pytest.mark.parametrize('transport', ['rest', 'jsonrpc'])
@pytest.mark.parametrize('endpoint_ver', ['0.3', '1.0'])
@pytest.mark.parametrize('is_streaming', [False, True])
@pytest.mark.parametrize(
    'header_val, should_succeed',
    [
        (None, '0.3'),
        ('0.3', '0.3'),
        ('1.0', '1.0'),
        ('1.2', '1.0'),
        ('2', 'none'),
        ('INVALID', 'none'),
    ],
)
def test_version_header_integration(
    client, transport, endpoint_ver, is_streaming, header_val, should_succeed
):
    headers = {}
    if header_val is not None:
        headers[VERSION_HEADER] = header_val

    expect_success = endpoint_ver == should_succeed

    if transport == 'rest':
        if endpoint_ver == '0.3':
            url = (
                '/rest/v1/message:stream'
                if is_streaming
                else '/rest/v1/message:send'
            )
        else:
            url = (
                '/rest/message:stream' if is_streaming else '/rest/message:send'
            )

        payload = {
            'message': {
                'messageId': 'msg1',
                'role': 'ROLE_USER' if endpoint_ver == '1.0' else 'user',
                'parts': [{'text': 'hello'}] if endpoint_ver == '1.0' else None,
                'content': [{'text': 'hello'}]
                if endpoint_ver == '0.3'
                else None,
            }
        }
        if endpoint_ver == '0.3':
            del payload['message']['parts']
        else:
            del payload['message']['content']

        if is_streaming:
            headers['Accept'] = 'text/event-stream'
            with client.stream(
                'POST', url, json=payload, headers=headers
            ) as response:
                response.read()

                if expect_success:
                    assert response.status_code == 200, response.text
                else:
                    assert response.status_code == 400, response.text
        else:
            response = client.post(url, json=payload, headers=headers)
            if expect_success:
                assert response.status_code == 200, response.text
            else:
                assert response.status_code == 400, response.text

    else:
        url = '/jsonrpc'
        if endpoint_ver == '0.3':
            payload = {
                'jsonrpc': '2.0',
                'id': '1',
                'method': 'message/stream' if is_streaming else 'message/send',
                'params': {
                    'message': {
                        'messageId': 'msg1',
                        'role': 'user',
                        'parts': [{'text': 'hello'}],
                    }
                },
            }
        else:
            payload = {
                'jsonrpc': '2.0',
                'id': '1',
                'method': 'SendStreamingMessage'
                if is_streaming
                else 'SendMessage',
                'params': {
                    'message': {
                        'messageId': 'msg1',
                        'role': 'ROLE_USER',
                        'parts': [{'text': 'hello'}],
                    }
                },
            }

        if is_streaming:
            headers['Accept'] = 'text/event-stream'
            with client.stream(
                'POST', url, json=payload, headers=headers
            ) as response:
                response.read()

                if expect_success:
                    assert response.status_code == 200, response.text
                    assert (
                        'result' in response.text or 'task' in response.text
                    ), response.text
                else:
                    assert response.status_code == 200
                    assert 'error' in response.text.lower(), response.text
        else:
            response = client.post(url, json=payload, headers=headers)
            assert response.status_code == 200, response.text
            resp_data = response.json()
            if expect_success:
                assert 'result' in resp_data, resp_data
            else:
                assert 'error' in resp_data, resp_data
                expected_code = -32603 if endpoint_ver == '0.3' else -32009
                assert resp_data['error']['code'] == expected_code
