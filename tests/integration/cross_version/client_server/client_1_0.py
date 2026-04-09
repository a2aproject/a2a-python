import argparse
import asyncio
import grpc
import httpx
import sys
from uuid import uuid4

from a2a.client import ClientConfig, create_client
from a2a.utils import TransportProtocol
from a2a.types import (
    Message,
    Part,
    Role,
    GetTaskRequest,
    CancelTaskRequest,
    SubscribeToTaskRequest,
    GetExtendedAgentCardRequest,
    SendMessageRequest,
    TaskPushNotificationConfig,
    GetTaskPushNotificationConfigRequest,
    ListTaskPushNotificationConfigsRequest,
    DeleteTaskPushNotificationConfigRequest,
    TaskState,
)
from a2a.client.errors import A2AClientError
from google.protobuf.struct_pb2 import Struct, Value


async def test_send_message_stream(client):
    print('Testing send_message (streaming)...')

    s = Struct()
    s.update({'key': 'value'})

    msg = Message(
        role=Role.ROLE_USER,
        message_id=f'stream-{uuid4()}',
        parts=[
            Part(text='stream'),
            Part(url='https://example.com/file.txt', media_type='text/plain'),
            Part(raw=b'hello', media_type='application/octet-stream'),
            Part(data=Value(struct_value=s)),
        ],
        metadata={'test_key': 'full_message'},
    )
    events = []

    async for event in client.send_message(
        request=SendMessageRequest(message=msg)
    ):
        events.append(event)
        break

    assert len(events) > 0, 'Expected at least one event'
    first_event = events[0]

    # In v1.0 SDK, send_message returns StreamResponse
    stream_response = first_event

    # Try to find task_id in the oneof fields of StreamResponse
    task_id = 'unknown'
    if stream_response.HasField('task'):
        task_id = stream_response.task.id
    elif stream_response.HasField('message'):
        task_id = stream_response.message.task_id
    elif stream_response.HasField('status_update'):
        task_id = stream_response.status_update.task_id
    elif stream_response.HasField('artifact_update'):
        task_id = stream_response.artifact_update.task_id

    print(f'Success: send_message (streaming) passed. Task ID: {task_id}')
    return task_id


async def test_send_message_sync(url, protocol_enum):
    print('Testing send_message (synchronous)...')
    config = ClientConfig()
    config.httpx_client = httpx.AsyncClient(timeout=30.0)
    config.grpc_channel_factory = grpc.aio.insecure_channel
    config.supported_protocol_bindings = [protocol_enum]
    config.streaming = False

    client = await create_client(url, client_config=config)
    msg = Message(
        role=Role.ROLE_USER,
        message_id=f'sync-{uuid4()}',
        parts=[Part(text='sync')],
        metadata={'test_key': 'simple_message'},
    )

    async for event in client.send_message(
        request=SendMessageRequest(message=msg)
    ):
        assert event is not None
        stream_response = event

        status = None
        if stream_response.HasField('task'):
            status = stream_response.task.status
        elif stream_response.HasField('status_update'):
            status = stream_response.status_update.status

        if status and status.state == TaskState.TASK_STATE_COMPLETED:
            metadata = dict(status.message.metadata)
            assert metadata.get('response_key') == 'response_value', (
                f'Missing response metadata: {metadata}'
            )
            assert status.message.parts[0].text == 'done'
            break
        else:
            print(f'Ignore message: {stream_response}')

    print(f'Success: send_message (synchronous) passed.')


async def test_get_task(client, task_id):
    print(f'Testing get_task ({task_id})...')
    task = await client.get_task(request=GetTaskRequest(id=task_id))
    assert task.id == task_id

    user_msgs = [m for m in task.history if m.role == Role.ROLE_USER]
    assert user_msgs, 'Expected at least one ROLE_USER message in task history'
    client_msg = user_msgs[0]

    assert len(client_msg.parts) == 4, (
        f'Expected 4 parts, got {len(client_msg.parts)}'
    )

    # 1. text part
    assert client_msg.parts[0].text == 'stream', (
        f"Expected 'stream', got {client_msg.parts[0].text}"
    )

    # 2. uri part
    assert client_msg.parts[1].url == 'https://example.com/file.txt'

    # 3. bytes part
    assert client_msg.parts[2].raw == b'hello'

    # 4. data part
    data_dict = dict(client_msg.parts[3].data.struct_value.fields)
    assert data_dict['key'].string_value == 'value'

    print('Success: get_task passed.')


async def test_cancel_task(client, task_id):
    print(f'Testing cancel_task ({task_id})...')
    await client.cancel_task(request=CancelTaskRequest(id=task_id))
    task = await client.get_task(request=GetTaskRequest(id=task_id))
    assert task.status.state == TaskState.TASK_STATE_CANCELED, (
        f'Expected {TaskState.TASK_STATE_CANCELED}, got {task.status.state}'
    )
    print('Success: cancel_task passed.')


async def test_subscribe(client, task_id):
    print(f'Testing subscribe ({task_id})...')
    has_artifact = False
    async for event in client.subscribe(
        request=SubscribeToTaskRequest(id=task_id)
    ):
        assert event is not None
        stream_response = event
        if stream_response.HasField('artifact_update'):
            has_artifact = True
            artifact = stream_response.artifact_update.artifact
            assert artifact.name == 'test-artifact'
            val = artifact.metadata['artifact_key']
            if hasattr(val, 'string_value'):
                assert val.string_value == 'artifact_value'
            else:
                assert val == 'artifact_value'
            assert artifact.parts[0].text == 'artifact-chunk'
            print('Success: received artifact update.')

        if has_artifact:
            break
    print('Success: subscribe passed.')


async def test_list_tasks(client, server_name):
    from a2a.types import ListTasksRequest
    from a2a.client.errors import A2AClientError

    print('Testing list_tasks...')
    try:
        resp = await client.list_tasks(request=ListTasksRequest())
        assert resp is not None
        print(f'Success: list_tasks returned {len(resp.tasks)} tasks')
    except NotImplementedError as e:
        if server_name == 'Server 0.3':
            print(f'Success: list_tasks gracefully failed on 0.3 Server: {e}')
        else:
            raise e


async def test_get_extended_agent_card(client):
    print('Testing get_extended_agent_card...')
    card = await client.get_extended_agent_card(
        request=GetExtendedAgentCardRequest()
    )
    assert card is not None
    assert card.name in ('Server 0.3', 'Server 1.0')
    assert card.version == '1.0.0'
    assert 'Server running on a2a v' in card.description

    assert card.capabilities is not None
    assert card.capabilities.streaming is True
    assert card.capabilities.push_notifications is True

    if card.name == 'Server 1.0':
        assert len(card.supported_interfaces) == 4
        assert card.capabilities.extended_agent_card in (False, None)
    else:
        assert len(card.supported_interfaces) > 0
        assert card.capabilities.extended_agent_card in (False, None)

    print(f'Success: get_extended_agent_card passed.')
    return card.name


async def test_push_notification_lifecycle(client, task_id, server_name):
    print(f'Testing Push Notification lifecycle for task {task_id}...')
    config_id = f'push-{uuid4()}'

    # 1. Create
    task_push_cfg = TaskPushNotificationConfig(
        task_id=task_id, id=config_id, url='http://127.0.0.1:9999/webhook'
    )

    created = await client.create_task_push_notification_config(
        request=task_push_cfg
    )
    assert created.id == config_id
    print('Success: create_task_push_notification_config passed.')

    # 2. Get
    get_req = GetTaskPushNotificationConfigRequest(
        task_id=task_id, id=config_id
    )
    fetched = await client.get_task_push_notification_config(request=get_req)
    assert fetched.id == config_id
    print('Success: get_task_push_notification_config passed.')

    # 3. List
    try:
        list_req = ListTaskPushNotificationConfigsRequest(task_id=task_id)
        listed = await client.list_task_push_notification_configs(
            request=list_req
        )
        assert any(c.id == config_id for c in listed.configs)
    except (NotImplementedError, A2AClientError) as e:
        if server_name == 'Server 0.3':
            print(
                'EXPECTED: list_task_push_notification_configs not implemented'
            )
        else:
            raise e
    print('Success: list_task_push_notification_configs passed.')

    try:
        # 4. Delete
        del_req = DeleteTaskPushNotificationConfigRequest(
            task_id=task_id, id=config_id
        )
        await client.delete_task_push_notification_config(request=del_req)
        print('Success: delete_task_push_notification_config passed.')

        # Verify deletion
        listed_after = await client.list_task_push_notification_configs(
            request=list_req
        )
        assert not any(c.id == config_id for c in listed_after.configs)
        print('Success: verified deletion.')
    except (NotImplementedError, A2AClientError) as e:
        if server_name == 'Server 0.3':
            print(
                'EXPECTED: delete_task_push_notification_config not implemented'
            )
        else:
            raise e


async def run_client(url: str, protocol: str):
    protocol_enum_map = {
        'jsonrpc': TransportProtocol.JSONRPC,
        'rest': TransportProtocol.HTTP_JSON,
        'grpc': TransportProtocol.GRPC,
    }
    protocol_enum = protocol_enum_map[protocol]

    config = ClientConfig()
    config.httpx_client = httpx.AsyncClient(timeout=30.0)
    config.grpc_channel_factory = grpc.aio.insecure_channel
    config.supported_protocol_bindings = [protocol_enum]
    config.streaming = True

    client = await create_client(url, client_config=config)

    # 1. Get Extended Agent Card
    server_name = await test_get_extended_agent_card(client)

    # 1.5. List Tasks
    await test_list_tasks(client, server_name)

    # 2. Send Streaming Message
    task_id = await test_send_message_stream(client)

    # 3. Get Task
    await test_get_task(client, task_id)

    # 3.5 Push Notification Lifecycle
    await test_push_notification_lifecycle(client, task_id, server_name)

    # 4. Subscribe to Task
    await test_subscribe(client, task_id)

    # 5. Cancel Task
    await test_cancel_task(client, task_id)

    # 6. Send Sync Message
    await test_send_message_sync(url, protocol_enum)


def main():
    print('Starting client_1_0...')

    parser = argparse.ArgumentParser()
    parser.add_argument('--url', type=str, required=True)
    parser.add_argument('--protocols', type=str, nargs='+', required=True)
    args = parser.parse_args()

    failed = False
    for protocol in args.protocols:
        print(f'\n=== Testing protocol: {protocol} ===')
        try:
            asyncio.run(run_client(args.url, protocol))
        except Exception as e:
            import traceback

            traceback.print_exc()
            print(f'FAILED protocol {protocol}: {e}')
            failed = True

    if failed:
        sys.exit(1)


if __name__ == '__main__':
    main()
