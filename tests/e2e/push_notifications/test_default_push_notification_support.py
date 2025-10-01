import asyncio
import time
import uuid

from multiprocessing import Lock

import httpx
import pytest
import pytest_asyncio

from agent_app import create_agent_app
from notifications_app import create_notifications_app
from utils import (
    create_app_process,
    find_free_port,
    wait_for_server_ready,
)


@pytest.fixture(scope='session')
def port_lock():
    """Multiprocessing lock for acquiring available ephemeral ports."""
    return Lock()


@pytest.fixture(scope='module')
def notifications_server(port_lock):
    """
    Starts a simple push notifications injesting server and yields its URL.
    """
    with port_lock:
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
def agent_server(
    port_lock,
    notifications_client: httpx.AsyncClient,
):
    """Starts a test agent server and yields its URL."""
    with port_lock:
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
    notifications_server: str, agent_server: str, http_client: httpx.AsyncClient
):
    """
    Tests push notification triggering for in-message push notification config.
    """
    # Send a message with a push notification config.
    response = await http_client.post(
        f'{agent_server}/v1/message:send',
        json={
            'configuration': {
                'pushNotification': {
                    'id': 'n-1',
                    'url': f'{notifications_server}/notifications',
                    'token': uuid.uuid4().hex,
                },
            },
            'request': {
                'messageId': 'r-1',
                'role': 'ROLE_USER',
                'content': [{'text': 'Hello Agent!'}],
            },
        },
    )
    assert response.status_code == 200
    task_id = response.json()['task']['id']
    assert task_id is not None

    # Retrive and check notifcations.
    notifications = await wait_for_n_notifications(
        http_client,
        f'{notifications_server}/tasks/{task_id}/notifications',
        n=2,
    )
    states = [notification['status']['state'] for notification in notifications]
    assert 'completed' in states
    assert 'submitted' in states


@pytest.mark.asyncio
async def test_notification_triggering_after_config_change_e2e(
    notifications_server: str, agent_server: str, http_client: httpx.AsyncClient
):
    """
    Tests notification triggering after setting the push notificaiton config in a seperate call.
    """
    # Send an initial message without the push notification config.
    response = await http_client.post(
        f'{agent_server}/v1/message:send',
        json={
            'request': {
                'messageId': 'r-1',
                'role': 'ROLE_USER',
                'content': [{'text': 'How are you?'}],
            },
        },
    )
    assert response.status_code == 200
    assert response.json()['task']['id'] is not None
    task_id = response.json()['task']['id']

    # Get the task to make sure that further input is required.
    response = await http_client.get(f'{agent_server}/v1/tasks/{task_id}')
    assert response.status_code == 200
    assert response.json()['status']['state'] == 'TASK_STATE_INPUT_REQUIRED'

    # Set a push notification config.
    response = await http_client.post(
        f'{agent_server}/v1/tasks/{task_id}/pushNotificationConfigs',
        json={
            'parent': f'tasks/{task_id}',
            'configId': uuid.uuid4().hex,
            'config': {
                'name': 'test-config',
                'pushNotificationConfig': {
                    'id': 'n-2',
                    'url': f'{notifications_server}/notifications',
                    'token': uuid.uuid4().hex,
                },
            },
        },
    )
    assert response.status_code == 200

    # Send a follow-up message that should trigger a push notification.
    response = await http_client.post(
        f'{agent_server}/v1/message:send',
        json={
            'request': {
                'taskId': task_id,
                'messageId': 'r-2',
                'role': 'ROLE_USER',
                'content': [{'text': 'Good'}],
            },
        },
    )
    assert response.status_code == 200

    # Retrive and check the notification.
    notifications = await wait_for_n_notifications(
        http_client,
        f'{notifications_server}/tasks/{task_id}/notifications',
        n=1,
    )
    assert notifications[0]['status']['state'] == 'completed'


async def wait_for_n_notifications(
    http_client: httpx.AsyncClient,
    url: str,
    n: int,
    timeout: int = 3,
):
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
            return notifications
        if time.time() - start_time > timeout:
            raise TimeoutError(
                f'Notification retrieval timed out. Got {len(notifications)} notifications, want {n}.'
            )
        await asyncio.sleep(0.1)
