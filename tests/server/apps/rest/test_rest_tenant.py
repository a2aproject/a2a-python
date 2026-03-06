import pytest
from unittest.mock import MagicMock
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from a2a.server.apps.rest.fastapi_app import A2ARESTFastAPIApplication
from a2a.server.request_handlers.request_handler import RequestHandler
from a2a.types.a2a_pb2 import (
    AgentCard,
    ListTaskPushNotificationConfigsResponse,
    ListTasksResponse,
    Message,
    Part,
    Role,
    Task,
    TaskPushNotificationConfig,
)


@pytest.fixture
async def agent_card() -> AgentCard:
    mock_agent_card = MagicMock(spec=AgentCard)
    mock_agent_card.url = 'http://mockurl.com'
    mock_capabilities = MagicMock()
    mock_capabilities.streaming = False
    mock_capabilities.push_notifications = True
    mock_capabilities.extended_agent_card = True
    mock_agent_card.capabilities = mock_capabilities
    return mock_agent_card


@pytest.fixture
async def request_handler() -> RequestHandler:
    handler = MagicMock(spec=RequestHandler)
    # Setup default return values for all handlers
    handler.on_message_send.return_value = Message(
        message_id='test',
        role=Role.ROLE_AGENT,
        parts=[Part(text='response message')],
    )
    handler.on_cancel_task.return_value = Task(id='1')
    handler.on_get_task.return_value = Task(id='1')
    handler.on_list_tasks.return_value = ListTasksResponse()
    handler.on_create_task_push_notification_config.return_value = (
        TaskPushNotificationConfig()
    )
    handler.on_get_task_push_notification_config.return_value = (
        TaskPushNotificationConfig()
    )
    handler.on_list_task_push_notification_configs.return_value = (
        ListTaskPushNotificationConfigsResponse()
    )
    handler.on_delete_task_push_notification_config.return_value = None
    return handler


@pytest.fixture
async def extended_card_modifier() -> MagicMock:
    modifier = MagicMock()
    modifier.return_value = AgentCard()
    return modifier


@pytest.fixture
async def app(
    agent_card: AgentCard,
    request_handler: RequestHandler,
    extended_card_modifier: MagicMock,
) -> FastAPI:
    return A2ARESTFastAPIApplication(
        agent_card,
        request_handler,
        extended_card_modifier=extended_card_modifier,
    ).build(agent_card_url='/well-known/agent.json', rpc_url='')


@pytest.fixture
async def client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url='http://test')


@pytest.mark.parametrize(
    'path_template, method, handler_method_name, json_body',
    [
        ('/message:send', 'POST', 'on_message_send', {'message': {}}),
        ('/tasks/1:cancel', 'POST', 'on_cancel_task', None),
        ('/tasks/1', 'GET', 'on_get_task', None),
        ('/tasks', 'GET', 'on_list_tasks', None),
        (
            '/tasks/1/pushNotificationConfigs/p1',
            'GET',
            'on_get_task_push_notification_config',
            None,
        ),
        (
            '/tasks/1/pushNotificationConfigs/p1',
            'DELETE',
            'on_delete_task_push_notification_config',
            None,
        ),
        (
            '/tasks/1/pushNotificationConfigs',
            'POST',
            'on_create_task_push_notification_config',
            {'config': {'url': 'http://foo'}},
        ),
        (
            '/tasks/1/pushNotificationConfigs',
            'GET',
            'on_list_task_push_notification_configs',
            None,
        ),
    ],
)
@pytest.mark.anyio
async def test_tenant_extraction_parametrized(
    client: AsyncClient,
    request_handler: MagicMock,
    extended_card_modifier: MagicMock,
    path_template: str,
    method: str,
    handler_method_name: str,
    json_body: dict | None,
) -> None:
    """Test tenant extraction for standard REST endpoints."""
    # Test with tenant
    tenant = 'my-tenant'
    tenant_path = f'/{tenant}{path_template}'

    response = await client.request(method, tenant_path, json=json_body)
    response.raise_for_status()

    # Verify handler call
    handler_mock = getattr(request_handler, handler_method_name)

    assert handler_mock.called
    args, _ = handler_mock.call_args
    context = args[1]
    assert context.tenant == tenant

    # Reset mock for non-tenant test
    handler_mock.reset_mock()

    # Test without tenant
    response = await client.request(method, path_template, json=json_body)
    response.raise_for_status()

    # Verify context.tenant == ""
    assert handler_mock.called
    args, _ = handler_mock.call_args
    context = args[1]
    assert context.tenant == ''


@pytest.mark.anyio
async def test_tenant_extraction_extended_agent_card(
    client: AsyncClient,
    extended_card_modifier: MagicMock,
) -> None:
    """Test tenant extraction specifically for extendedAgentCard endpoint.

    This verifies that `extended_card_modifier` receives the correct context
    including the tenant, confirming that `_build_call_context` is used correctly.
    """
    # Test with tenant
    tenant = 'my-tenant'
    tenant_path = f'/{tenant}/extendedAgentCard'

    response = await client.get(tenant_path)
    response.raise_for_status()

    # Verify extended_card_modifier called with tenant context
    assert extended_card_modifier.called
    args, _ = extended_card_modifier.call_args
    # args[0] is card_to_serve, args[1] is context
    context = args[1]
    assert context.tenant == tenant

    # Reset mock for non-tenant test
    extended_card_modifier.reset_mock()

    # Test without tenant
    response = await client.get('/extendedAgentCard')
    response.raise_for_status()

    # Verify extended_card_modifier called with empty tenant context
    assert extended_card_modifier.called
    args, _ = extended_card_modifier.call_args
    context = args[1]
    assert context.tenant == ''
