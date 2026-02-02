import asyncio
import time
import uuid

import httpx
import pytest
import pytest_asyncio

from agent_app import create_agent_app
from notifications_app import Notification, create_notifications_app
from utils import (
    create_app_process,
    find_free_port,
    wait_for_server_ready,
)

from a2a.client import (
    ClientConfig,
    ClientFactory,
    minimal_agent_card,
)
from a2a.types import (
    Message,
    Part,
    PushNotificationConfig,
    Role,
    Task,
    TaskPushNotificationConfig,
    TaskState,
    TextPart,
    TransportProtocol,
)


@pytest.fixture(scope='module')
def notifications_server():
    """
    Starts a simple push notifications ingesting server and yields its URL.
    """
    host = '127.0.0.1'
    port = find_free_port()
    url = f'http://{host}:{port}'

    process = create_app_process(create_notifications_app(), host, port)
    process.start()
    try:
        wait_for_server_ready(f'{url}/health')
    except TimeoutError as e:
        process.terminate()
        raise e

    yield url

    process.terminate()
    process.join()


@pytest_asyncio.fixture(scope='module')
async def notifications_client():
    """An async client fixture for calling the notifications server."""
    async with httpx.AsyncClient() as client:
        yield client


@pytest.fixture(scope='module')
def agent_server(notifications_client: httpx.AsyncClient):
    """Starts a test agent server and yields its URL."""
    host = '127.0.0.1'
    port = find_free_port()
    url = f'http://{host}:{port}'

    process = create_app_process(
        create_agent_app(url, notifications_client), host, port
    )
    process.start()
    try:
        wait_for_server_ready(f'{url}/v1/card')
    except TimeoutError as e:
        process.terminate()
        raise e

    yield url

    process.terminate()
    process.join()


@pytest_asyncio.fixture(scope='function')
async def http_client():
    """An async client fixture for test functions."""
    async with httpx.AsyncClient() as client:
        yield client


@pytest.mark.asyncio
async def test_notification_triggering_with_in_message_config_e2e(
    notifications_server: str,
    agent_server: str,
    http_client: httpx.AsyncClient,
):
    """
    Tests push notification triggering for in-message push notification config.
    """
    # Create an A2A client with a push notification config.
    token = uuid.uuid4().hex
    a2a_client = ClientFactory(
        ClientConfig(
            supported_transports=[TransportProtocol.http_json],
            push_notification_configs=[
                PushNotificationConfig(
                    id='in-message-config',
                    url=f'{notifications_server}/notifications',
                    token=token,
                )
            ],
        )
    ).create(minimal_agent_card(agent_server, [TransportProtocol.http_json]))

    # Send a message and extract the returned task.
    responses = [
        response
        async for response in a2a_client.send_message(
            Message(
                message_id='hello-agent',
                parts=[Part(root=TextPart(text='Hello Agent!'))],
                role=Role.user,
            )
        )
    ]
    assert len(responses) == 1
    assert isinstance(responses[0], tuple)
    assert isinstance(responses[0][0], Task)
    task = responses[0][0]

    # Verify a single notification was sent.
    notifications = await wait_for_n_notifications(
        http_client,
        f'{notifications_server}/tasks/{task.id}/notifications',
        n=1,
    )
    assert notifications[0].token == token
    assert notifications[0].task.id == task.id
    assert notifications[0].task.status.state == 'completed'


@pytest.mark.asyncio
async def test_notification_triggering_after_config_change_e2e(
    notifications_server: str, agent_server: str, http_client: httpx.AsyncClient
):
    """
    Tests notification triggering after setting the push notification config in a separate call.
    """
    # Configure an A2A client without a push notification config.
    a2a_client = ClientFactory(
        ClientConfig(
            supported_transports=[TransportProtocol.http_json],
        )
    ).create(minimal_agent_card(agent_server, [TransportProtocol.http_json]))

    # Send a message and extract the returned task.
    responses = [
        response
        async for response in a2a_client.send_message(
            Message(
                message_id='how-are-you',
                parts=[Part(root=TextPart(text='How are you?'))],
                role=Role.user,
            )
        )
    ]
    assert len(responses) == 1
    assert isinstance(responses[0], tuple)
    assert isinstance(responses[0][0], Task)
    task = responses[0][0]
    assert task.status.state == TaskState.input_required

    # Verify that no notification has been sent yet.
    response = await http_client.get(
        f'{notifications_server}/tasks/{task.id}/notifications'
    )
    assert response.status_code == 200
    assert len(response.json().get('notifications', [])) == 0

    # Set the push notification config.
    token = uuid.uuid4().hex
    await a2a_client.set_task_callback(
        TaskPushNotificationConfig(
            task_id=task.id,
            push_notification_config=PushNotificationConfig(
                id='after-config-change',
                url=f'{notifications_server}/notifications',
                token=token,
            ),
        )
    )

    # Send another message that should trigger a push notification.
    responses = [
        response
        async for response in a2a_client.send_message(
            Message(
                task_id=task.id,
                message_id='good',
                parts=[Part(root=TextPart(text='Good'))],
                role=Role.user,
            )
        )
    ]
    assert len(responses) == 1

    # Verify that the push notification was sent.
    notifications = await wait_for_n_notifications(
        http_client,
        f'{notifications_server}/tasks/{task.id}/notifications',
        n=1,
    )
    assert notifications[0].task.id == task.id
    assert notifications[0].task.status.state == 'completed'
    assert notifications[0].token == token


async def wait_for_n_notifications(
    http_client: httpx.AsyncClient,
    url: str,
    n: int,
    timeout: int = 3,
) -> list[Notification]:
    """
    Queries the notification URL until the desired number of notifications
    is received or the timeout is reached.
    """
    start_time = time.time()
    notifications = []
    while True:
        response = await http_client.get(url)
        assert response.status_code == 200
        notifications = response.json()['notifications']
        if len(notifications) == n:
            return [Notification.model_validate(n) for n in notifications]
        if time.time() - start_time > timeout:
            raise TimeoutError(
                f'Notification retrieval timed out. Got {len(notifications)} notification(s), want {n}. Retrieved notifications: {notifications}.'
            )
        await asyncio.sleep(0.1)
