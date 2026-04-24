import asyncio
import time
import uuid

import httpx
import pytest
import pytest_asyncio

from .agent_app import create_agent_app, create_multi_user_agent_app
from .notifications_app import Notification, create_notifications_app
from .utils import (
    create_app_process,
    find_free_port,
    wait_for_server_ready,
)

from a2a.client import (
    ClientConfig,
    ClientFactory,
    minimal_agent_card,
)
from a2a.utils.constants import TransportProtocol
from a2a.types.a2a_pb2 import (
    ListTaskPushNotificationConfigsRequest,
    Message,
    Part,
    Role,
    SendMessageConfiguration,
    SendMessageRequest,
    Task,
    TaskPushNotificationConfig,
    TaskState,
)


_TEST_USER_HEADER = 'x-test-user'


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
        wait_for_server_ready(
            f'{url}/extendedAgentCard', headers={'A2A-Version': '1.0'}
        )
    except TimeoutError as e:
        process.terminate()
        raise e

    yield url

    process.terminate()
    process.join()


@pytest.fixture(scope='module')
def multi_user_agent_server(notifications_client: httpx.AsyncClient):
    """Starts the multi-user variant of the test agent server.

    This variant reads identity from an x-test-user request header
    and uses a TaskStore whose owner resolver returns a constant, so
    every authenticated user can see every task. It runs on its own
    port alongside the single-user agent_server fixture; the
    notifications_server is shared (notifications include the
    task_id and per-config token, so collisions are avoided).
    """
    host = '127.0.0.1'
    port = find_free_port()
    url = f'http://{host}:{port}'

    process = create_app_process(
        create_multi_user_agent_app(url, notifications_client), host, port
    )
    process.start()
    try:
        wait_for_server_ready(
            f'{url}/extendedAgentCard',
            headers={'A2A-Version': '1.0', _TEST_USER_HEADER: 'health-check'},
        )
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
            supported_protocol_bindings=[TransportProtocol.HTTP_JSON],
            push_notification_config=TaskPushNotificationConfig(
                id='in-message-config',
                url=f'{notifications_server}/notifications',
                token=token,
            ),
        )
    ).create(minimal_agent_card(agent_server, [TransportProtocol.HTTP_JSON]))

    # Send a message and extract the returned task.
    responses = [
        response
        async for response in a2a_client.send_message(
            SendMessageRequest(
                message=Message(
                    message_id='hello-agent',
                    parts=[Part(text='Hello Agent!')],
                    role=Role.ROLE_USER,
                )
            )
        )
    ]
    assert len(responses) == 1
    stream_response = responses[0]
    assert stream_response.HasField('task')
    task = stream_response.task

    # Verify a single notification was sent.
    notifications = await wait_for_n_notifications(
        http_client,
        f'{notifications_server}/{task.id}/notifications',
        n=2,
    )
    assert notifications[0].token == token

    # Verify exactly two consecutive events: SUBMITTED -> COMPLETED
    assert len(notifications) == 2

    # 1. First event: SUBMITTED (Task)
    event0 = notifications[0].event
    state0 = event0['task'].get('status', {}).get('state')
    assert state0 == 'TASK_STATE_SUBMITTED'

    # 2. Second event: COMPLETED (TaskStatusUpdateEvent)
    event1 = notifications[1].event
    state1 = event1['status_update'].get('status', {}).get('state')
    assert state1 == 'TASK_STATE_COMPLETED'


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
            supported_protocol_bindings=[TransportProtocol.HTTP_JSON],
        )
    ).create(minimal_agent_card(agent_server, [TransportProtocol.HTTP_JSON]))

    # Send a message and extract the returned task.
    responses = [
        response
        async for response in a2a_client.send_message(
            SendMessageRequest(
                message=Message(
                    message_id='how-are-you',
                    parts=[Part(text='How are you?')],
                    role=Role.ROLE_USER,
                ),
                configuration=SendMessageConfiguration(),
            )
        )
    ]
    assert len(responses) == 1
    stream_response = responses[0]
    assert stream_response.HasField('task')
    task = stream_response.task
    assert task.status.state == TaskState.TASK_STATE_INPUT_REQUIRED

    # Verify that no notification has been sent yet.
    response = await http_client.get(
        f'{notifications_server}/{task.id}/notifications'
    )
    assert response.status_code == 200
    assert len(response.json().get('notifications', [])) == 0

    # Set the push notification config.
    token = uuid.uuid4().hex
    await a2a_client.create_task_push_notification_config(
        TaskPushNotificationConfig(
            task_id=f'{task.id}',
            id='after-config-change',
            url=f'{notifications_server}/notifications',
            token=token,
        )
    )

    # Send another message that should trigger a push notification.
    responses = [
        response
        async for response in a2a_client.send_message(
            SendMessageRequest(
                message=Message(
                    task_id=task.id,
                    message_id='good',
                    parts=[Part(text='Good')],
                    role=Role.ROLE_USER,
                ),
                configuration=SendMessageConfiguration(),
            )
        )
    ]
    assert len(responses) == 1

    # Verify that the push notification was sent.
    notifications = await wait_for_n_notifications(
        http_client,
        f'{notifications_server}/{task.id}/notifications',
        n=1,
    )
    event = notifications[0].event
    state = event['status_update'].get('status', {}).get('state', '')
    assert state == 'TASK_STATE_COMPLETED'
    assert notifications[0].token == token


@pytest.mark.asyncio
async def test_multi_registrar_fan_out_e2e(
    notifications_server: str,
    agent_server: str,
    http_client: httpx.AsyncClient,
):
    """Two pushNotificationConfigs registered for the same task both fire end-to-end.

    Exercises the dispatch fan-out across multiple registered configs
    over the real wire: each registered URL must receive a POST with
    its own token in the X-A2A-Notification-Token header.
    """
    # Configure an A2A client without a per-message push notification config
    # (we'll register configs explicitly after the task is created).
    a2a_client = ClientFactory(
        ClientConfig(
            supported_protocol_bindings=[TransportProtocol.HTTP_JSON],
        )
    ).create(minimal_agent_card(agent_server, [TransportProtocol.HTTP_JSON]))

    # Send an initial message that requires more input, so the task lingers
    # long enough for us to register multiple push configs against it.
    responses = [
        response
        async for response in a2a_client.send_message(
            SendMessageRequest(
                message=Message(
                    message_id='multi-fanout-init',
                    parts=[Part(text='How are you?')],
                    role=Role.ROLE_USER,
                ),
                configuration=SendMessageConfiguration(),
            )
        )
    ]
    assert len(responses) == 1
    stream_response = responses[0]
    assert stream_response.HasField('task')
    task = stream_response.task
    assert task.status.state == TaskState.TASK_STATE_INPUT_REQUIRED

    # Register two distinct push configs for the same task. Both share the
    # same registrar (this client), but use different config ids, URLs, and
    # tokens. Both must fire when the next event is dispatched.
    token_a = uuid.uuid4().hex
    token_b = uuid.uuid4().hex
    await a2a_client.create_task_push_notification_config(
        TaskPushNotificationConfig(
            task_id=task.id,
            id='registrar-a',
            url=f'{notifications_server}/notifications',
            token=token_a,
        )
    )
    await a2a_client.create_task_push_notification_config(
        TaskPushNotificationConfig(
            task_id=task.id,
            id='registrar-b',
            url=f'{notifications_server}/notifications',
            token=token_b,
        )
    )

    # Sanity: no notifications have fired yet.
    response = await http_client.get(
        f'{notifications_server}/{task.id}/notifications'
    )
    assert response.status_code == 200
    assert len(response.json().get('notifications', [])) == 0

    # Send a follow-up message that completes the task and triggers
    # dispatch. Both registered configs must receive a POST.
    responses = [
        response
        async for response in a2a_client.send_message(
            SendMessageRequest(
                message=Message(
                    task_id=task.id,
                    message_id='multi-fanout-complete',
                    parts=[Part(text='Good')],
                    role=Role.ROLE_USER,
                ),
                configuration=SendMessageConfiguration(),
            )
        )
    ]
    assert len(responses) == 1

    # Expect 2 notifications: one COMPLETED event, fanned out to 2 configs.
    notifications = await wait_for_n_notifications(
        http_client,
        f'{notifications_server}/{task.id}/notifications',
        n=2,
    )

    # Both tokens must appear exactly once.
    received_tokens = sorted(n.token for n in notifications)
    assert received_tokens == sorted([token_a, token_b])

    # Both notifications must carry the same COMPLETED event payload.
    for notification in notifications:
        state = (
            notification.event.get('status_update', {})
            .get('status', {})
            .get('state')
        )
        assert state == 'TASK_STATE_COMPLETED'


def _make_user_a2a_client(agent_server: str, user_name: str):
    """Builds an A2A client that identifies as user_name on every request.

    Identity is conveyed via a default header on the underlying
    httpx.AsyncClient; the multi-user agent app's context builder
    reads that header to populate ServerCallContext.user.
    """
    httpx_client = httpx.AsyncClient(headers={_TEST_USER_HEADER: user_name})
    return ClientFactory(
        ClientConfig(
            httpx_client=httpx_client,
            supported_protocol_bindings=[TransportProtocol.HTTP_JSON],
        )
    ).create(
        minimal_agent_card(agent_server, [TransportProtocol.HTTP_JSON])
    ), httpx_client


@pytest.mark.asyncio
async def test_alice_and_bob_both_receive_notifications_on_shared_task_e2e(
    notifications_server: str,
    multi_user_agent_server: str,
    http_client: httpx.AsyncClient,
):
    """Alice registers a webhook; Bob registers a webhook; both fire end-to-end.

    1. Alice creates a task (it lingers in INPUT_REQUIRED).
    2. Alice registers her own push config on the task.
    3. Bob (a different authenticated user, sharing access to the task)
       registers his own push config on the same task.
    4. Bob (the dispatcher, *not* the registrar of Alice's webhook)
       sends a follow-up message that completes the task.
    5. Both Alice's webhook and Bob's webhook receive a POST with their
       own respective tokens.

    Regression guard for the design's central guarantee: subscriptions
    fire on the registrar's behalf regardless of which user's action
    triggered the event. A regression that re-introduced
    dispatcher-context filtering on the dispatch path would drop one of
    the two notifications.
    """
    alice_client, alice_http = _make_user_a2a_client(
        multi_user_agent_server, 'alice'
    )
    bob_client, bob_http = _make_user_a2a_client(multi_user_agent_server, 'bob')

    try:
        responses = [
            response
            async for response in alice_client.send_message(
                SendMessageRequest(
                    message=Message(
                        message_id='shared-task-init',
                        parts=[Part(text='How are you?')],
                        role=Role.ROLE_USER,
                    ),
                )
            )
        ]
        assert len(responses) == 1
        assert responses[0].HasField('task')
        task = responses[0].task
        assert task.status.state == TaskState.TASK_STATE_INPUT_REQUIRED

        # 2. Alice registers her push config.
        alice_token = uuid.uuid4().hex
        await alice_client.create_task_push_notification_config(
            TaskPushNotificationConfig(
                task_id=task.id,
                id='alice-cfg',
                url=f'{notifications_server}/notifications',
                token=alice_token,
            )
        )

        # 3. Bob registers his push config on the same task.
        bob_token = uuid.uuid4().hex
        await bob_client.create_task_push_notification_config(
            TaskPushNotificationConfig(
                task_id=task.id,
                id='bob-cfg',
                url=f'{notifications_server}/notifications',
                token=bob_token,
            )
        )

        # Sanity: the per-user listing endpoints are owner-scoped --
        # Alice does not see Bob's config and vice-versa, even though
        # both can see the underlying task.
        #
        # The auto-registered empty config (see step 1 quirk note) lives
        # in Alice's partition under ``id == task_id``, so Alice's
        # listing contains ``{'alice-cfg', task.id}``; the key invariant
        # is that neither listing contains the other user's id or
        # token.
        alice_configs = await alice_client.list_task_push_notification_configs(
            ListTaskPushNotificationConfigsRequest(task_id=task.id)
        )
        alice_ids = {c.id for c in alice_configs.configs}
        assert 'alice-cfg' in alice_ids
        assert 'bob-cfg' not in alice_ids
        assert all(c.token != bob_token for c in alice_configs.configs)

        bob_configs = await bob_client.list_task_push_notification_configs(
            ListTaskPushNotificationConfigsRequest(task_id=task.id)
        )
        bob_ids = {c.id for c in bob_configs.configs}
        assert 'bob-cfg' in bob_ids
        assert 'alice-cfg' not in bob_ids
        assert all(c.token != alice_token for c in bob_configs.configs)

        # Sanity: no notifications have fired yet.
        response = await http_client.get(
            f'{notifications_server}/{task.id}/notifications'
        )
        assert response.status_code == 200
        assert len(response.json().get('notifications', [])) == 0

        # 4. Bob sends the follow-up message that completes the task.
        # Omit ``configuration`` for the same reason as step 1.
        responses = [
            response
            async for response in bob_client.send_message(
                SendMessageRequest(
                    message=Message(
                        task_id=task.id,
                        message_id='shared-task-complete',
                        parts=[Part(text='Good')],
                        role=Role.ROLE_USER,
                    ),
                )
            )
        ]
        assert len(responses) == 1

        # 5. Both Alice's and Bob's webhooks receive the COMPLETED event.
        notifications = await wait_for_n_notifications(
            http_client,
            f'{notifications_server}/{task.id}/notifications',
            n=2,
        )

        received_tokens = sorted(n.token for n in notifications)
        assert received_tokens == sorted([alice_token, bob_token])

        for notification in notifications:
            state = (
                notification.event.get('status_update', {})
                .get('status', {})
                .get('state')
            )
            assert state == 'TASK_STATE_COMPLETED'
    finally:
        await alice_http.aclose()
        await bob_http.aclose()


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
