import logging

from typing import Any
from unittest.mock import MagicMock

import pytest

from fastapi import FastAPI
from google.protobuf import json_format
from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette
from a2a.server.routes.rest_routes import create_rest_routes
from a2a.server.routes import create_agent_card_routes
from a2a.server.request_handlers.request_handler import RequestHandler
from a2a.types.a2a_pb2 import (
    AgentCard,
    Message as Message10,
    Part as Part10,
    Role as Role10,
    Task as Task10,
    TaskStatus as TaskStatus10,
    TaskState as TaskState10,
)
from a2a.compat.v0_3 import a2a_v0_3_pb2


logger = logging.getLogger(__name__)


@pytest.fixture
async def agent_card() -> AgentCard:
    mock_agent_card = MagicMock(spec=AgentCard)
    mock_agent_card.url = 'http://mockurl.com'

    # Mock the capabilities object with streaming disabled
    mock_capabilities = MagicMock()
    mock_capabilities.streaming = False
    mock_capabilities.push_notifications = True
    mock_capabilities.extended_agent_card = True
    mock_agent_card.capabilities = mock_capabilities

    return mock_agent_card


@pytest.fixture
async def request_handler() -> RequestHandler:
    return MagicMock(spec=RequestHandler)


@pytest.fixture
async def app(
    agent_card: AgentCard,
    request_handler: RequestHandler,
) -> Starlette:
    """Builds the Starlette application for testing."""
    request_handler._agent_card = agent_card
    rest_routes = create_rest_routes(
        request_handler=request_handler, enable_v0_3_compat=True
    )
    agent_card_routes = create_agent_card_routes(
        agent_card=agent_card, card_url='/well-known/agent.json'
    )
    return Starlette(routes=rest_routes + agent_card_routes)


@pytest.fixture
async def client(app: FastAPI) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=app), base_url='http://testapp'
    )


@pytest.mark.anyio
async def test_send_message_success_message_v03(
    client: AsyncClient, request_handler: MagicMock
) -> None:
    expected_response = a2a_v0_3_pb2.SendMessageResponse(
        msg=a2a_v0_3_pb2.Message(
            message_id='test',
            role=a2a_v0_3_pb2.Role.ROLE_AGENT,
            content=[a2a_v0_3_pb2.Part(text='response message')],
        ),
    )
    request_handler.on_message_send.return_value = Message10(
        message_id='test',
        role=Role10.ROLE_AGENT,
        parts=[Part10(text='response message')],
    )

    request = a2a_v0_3_pb2.SendMessageRequest(
        request=a2a_v0_3_pb2.Message(
            message_id='req',
            role=a2a_v0_3_pb2.Role.ROLE_USER,
            content=[a2a_v0_3_pb2.Part(text='hello')],
        ),
    )

    response = await client.post(
        '/v1/message:send', json=json_format.MessageToDict(request)
    )
    response.raise_for_status()

    actual_response = a2a_v0_3_pb2.SendMessageResponse()
    json_format.Parse(response.text, actual_response)
    assert expected_response == actual_response


@pytest.mark.anyio
async def test_send_message_success_task_v03(
    client: AsyncClient, request_handler: MagicMock
) -> None:
    expected_response = a2a_v0_3_pb2.SendMessageResponse(
        task=a2a_v0_3_pb2.Task(
            id='test_task_id',
            context_id='test_context_id',
            status=a2a_v0_3_pb2.TaskStatus(
                state=a2a_v0_3_pb2.TaskState.TASK_STATE_COMPLETED,
            ),
        ),
    )
    request_handler.on_message_send.return_value = Task10(
        id='test_task_id',
        context_id='test_context_id',
        status=TaskStatus10(
            state=TaskState10.TASK_STATE_COMPLETED,
        ),
    )

    request = a2a_v0_3_pb2.SendMessageRequest(
        request=a2a_v0_3_pb2.Message(),
    )

    response = await client.post(
        '/v1/message:send', json=json_format.MessageToDict(request)
    )
    response.raise_for_status()

    actual_response = a2a_v0_3_pb2.SendMessageResponse()
    json_format.Parse(response.text, actual_response)
    assert expected_response == actual_response


@pytest.mark.anyio
async def test_get_task_v03(
    client: AsyncClient, request_handler: MagicMock
) -> None:
    expected_response = a2a_v0_3_pb2.Task(
        id='test_task_id',
        context_id='test_context_id',
        status=a2a_v0_3_pb2.TaskStatus(
            state=a2a_v0_3_pb2.TaskState.TASK_STATE_COMPLETED,
        ),
    )
    request_handler.on_get_task.return_value = Task10(
        id='test_task_id',
        context_id='test_context_id',
        status=TaskStatus10(
            state=TaskState10.TASK_STATE_COMPLETED,
        ),
    )

    response = await client.get('/v1/tasks/test_task_id')
    response.raise_for_status()

    actual_response = a2a_v0_3_pb2.Task()
    json_format.Parse(response.text, actual_response)
    assert expected_response == actual_response


@pytest.mark.anyio
async def test_cancel_task_v03(
    client: AsyncClient, request_handler: MagicMock
) -> None:
    expected_response = a2a_v0_3_pb2.Task(
        id='test_task_id',
        context_id='test_context_id',
        status=a2a_v0_3_pb2.TaskStatus(
            state=a2a_v0_3_pb2.TaskState.TASK_STATE_CANCELLED,
        ),
    )
    request_handler.on_cancel_task.return_value = Task10(
        id='test_task_id',
        context_id='test_context_id',
        status=TaskStatus10(
            state=TaskState10.TASK_STATE_CANCELED,
        ),
    )

    response = await client.post('/v1/tasks/test_task_id:cancel')
    response.raise_for_status()

    actual_response = a2a_v0_3_pb2.Task()
    json_format.Parse(response.text, actual_response)
    assert expected_response == actual_response
