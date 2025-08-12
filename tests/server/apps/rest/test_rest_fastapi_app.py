import logging

from typing import Any
from unittest.mock import MagicMock

import pytest

from fastapi import FastAPI
from google.protobuf import json_format
from httpx import ASGITransport, AsyncClient

from a2a.grpc import a2a_pb2
from a2a.server.apps.rest import fastapi_app, rest_adapter
from a2a.server.apps.rest.fastapi_app import A2ARESTFastAPIApplication
from a2a.server.apps.rest.rest_adapter import RESTAdapter
from a2a.server.request_handlers.request_handler import RequestHandler
from a2a.types import (
    AgentCard,
    Message,
    Part,
    Role,
    Task,
    TaskState,
    TaskStatus,
    TextPart,
)


logger = logging.getLogger(__name__)


@pytest.fixture
async def agent_card() -> AgentCard:
    mock_agent_card = MagicMock(spec=AgentCard)
    mock_agent_card.url = 'http://mockurl.com'
    mock_agent_card.supports_authenticated_extended_card = False
    return mock_agent_card


@pytest.fixture
async def request_handler() -> RequestHandler:
    return MagicMock(spec=RequestHandler)


@pytest.fixture
async def app(
    agent_card: AgentCard, request_handler: RequestHandler
) -> FastAPI:
    """Builds the FastAPI application for testing."""

    return A2ARESTFastAPIApplication(agent_card, request_handler).build(
        agent_card_url='/well-known/agent.json', rpc_url=''
    )


@pytest.fixture
async def client(app: FastAPI) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=app), base_url='http://testapp'
    )


@pytest.fixture
def mark_pkg_starlette_not_installed():
    pkg_starlette_installed_flag = rest_adapter._package_starlette_installed
    rest_adapter._package_starlette_installed = False
    yield
    rest_adapter._package_starlette_installed = pkg_starlette_installed_flag


@pytest.fixture
def mark_pkg_fastapi_not_installed():
    pkg_fastapi_installed_flag = fastapi_app._package_fastapi_installed
    fastapi_app._package_fastapi_installed = False
    yield
    fastapi_app._package_fastapi_installed = pkg_fastapi_installed_flag


@pytest.mark.anyio
async def test_create_rest_adapter_with_present_deps_succeeds(
    agent_card: AgentCard, request_handler: RequestHandler
):
    try:
        _app = RESTAdapter(agent_card, request_handler)
    except ImportError:
        pytest.fail(
            'With packages starlette and see-starlette present, creating an'
            ' RESTAdapter instance should not raise ImportError'
        )


@pytest.mark.anyio
async def test_create_rest_adapter_with_missing_deps_raises_importerror(
    agent_card: AgentCard,
    request_handler: RequestHandler,
    mark_pkg_starlette_not_installed: Any,
):
    with pytest.raises(
        ImportError,
        match=(
            'Packages `starlette` and `sse-starlette` are required to use'
            ' the `RESTAdapter`.'
        ),
    ):
        _app = RESTAdapter(agent_card, request_handler)


@pytest.mark.anyio
async def test_create_a2a_rest_fastapi_app_with_present_deps_succeeds(
    agent_card: AgentCard, request_handler: RequestHandler
):
    try:
        _app = A2ARESTFastAPIApplication(agent_card, request_handler).build(
            agent_card_url='/well-known/agent.json', rpc_url=''
        )
    except ImportError:
        pytest.fail(
            'With the fastapi package present, creating a'
            ' A2ARESTFastAPIApplication instance should not raise ImportError'
        )


@pytest.mark.anyio
async def test_create_a2a_rest_fastapi_app_with_missing_deps_raises_importerror(
    agent_card: AgentCard,
    request_handler: RequestHandler,
    mark_pkg_fastapi_not_installed: Any,
):
    with pytest.raises(
        ImportError,
        match=(
            'The `fastapi` package is required to use the'
            ' `A2ARESTFastAPIApplication`'
        ),
    ):
        _app = A2ARESTFastAPIApplication(agent_card, request_handler).build(
            agent_card_url='/well-known/agent.json', rpc_url=''
        )


@pytest.mark.anyio
async def test_send_message_success_message(
    client: AsyncClient, request_handler: MagicMock
) -> None:
    expected_response = a2a_pb2.SendMessageResponse(
        msg=a2a_pb2.Message(
            message_id='test',
            role=a2a_pb2.Role.ROLE_AGENT,
            content=[
                a2a_pb2.Part(text='response message'),
            ],
        ),
    )
    request_handler.on_message_send.return_value = Message(
        message_id='test',
        role=Role.agent,
        parts=[Part(TextPart(text='response message'))],
    )

    request = a2a_pb2.SendMessageRequest(
        request=a2a_pb2.Message(),
        configuration=a2a_pb2.SendMessageConfiguration(),
    )
    # To see log output, run pytest with '--log-cli=true --log-cli-level=INFO'
    response = await client.post(
        '/v1/message:send', json=json_format.MessageToDict(request)
    )
    # request should always be successful
    response.raise_for_status()

    actual_response = a2a_pb2.SendMessageResponse()
    json_format.Parse(response.text, actual_response)
    assert expected_response == actual_response


@pytest.mark.anyio
async def test_send_message_success_task(
    client: AsyncClient, request_handler: MagicMock
) -> None:
    expected_response = a2a_pb2.SendMessageResponse(
        task=a2a_pb2.Task(
            id='test_task_id',
            context_id='test_context_id',
            status=a2a_pb2.TaskStatus(
                state=a2a_pb2.TaskState.TASK_STATE_COMPLETED,
                update=a2a_pb2.Message(
                    message_id='test',
                    role=a2a_pb2.ROLE_AGENT,
                    content=[
                        a2a_pb2.Part(text='response task message'),
                    ],
                ),
            ),
        ),
    )
    request_handler.on_message_send.return_value = Task(
        id='test_task_id',
        context_id='test_context_id',
        status=TaskStatus(
            state=TaskState.completed,
            message=Message(
                message_id='test',
                role=Role.agent,
                parts=[Part(TextPart(text='response task message'))],
            ),
        ),
    )

    request = a2a_pb2.SendMessageRequest(
        request=a2a_pb2.Message(),
        configuration=a2a_pb2.SendMessageConfiguration(),
    )
    # To see log output, run pytest with '--log-cli=true --log-cli-level=INFO'
    response = await client.post(
        '/v1/message:send', json=json_format.MessageToDict(request)
    )
    # request should always be successful
    response.raise_for_status()

    actual_response = a2a_pb2.SendMessageResponse()
    json_format.Parse(response.text, actual_response)
    assert expected_response == actual_response


if __name__ == '__main__':
    pytest.main([__file__])
