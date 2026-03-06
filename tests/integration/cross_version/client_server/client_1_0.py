import argparse
import asyncio
import grpc
import httpx
import sys
from uuid import uuid4

from a2a.client import ClientFactory, ClientConfig
from a2a.utils import TransportProtocol
from a2a.types import (
    Message,
    Part,
    Role,
    GetTaskRequest,
    CancelTaskRequest,
    SubscribeToTaskRequest,
    GetExtendedAgentCardRequest,
)


async def test_send_message_stream(client):
    print('Testing send_message (streaming)...')
    msg = Message(
        role=Role.ROLE_USER,
        message_id=f'stream-{uuid4()}',
        parts=[Part(text='stream')],
        metadata={'test_key': 'test_value'},
    )
    events = []

    async for event in client.send_message(request=msg):
        events.append(event)
        break

    assert len(events) > 0, 'Expected at least one event'
    first_event = events[0]

    # In v1.0 SDK, send_message returns tuple[StreamResponse, Task | None]
    stream_response = first_event[0]

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

    client = await ClientFactory.connect(url, client_config=config)
    msg = Message(
        role=Role.ROLE_USER,
        message_id=f'sync-{uuid4()}',
        parts=[Part(text='sync')],
        metadata={'test_key': 'test_value'},
    )

    async for event in client.send_message(request=msg):
        assert event is not None
        stream_response = event[0]

        # In v1.0, check task status in StreamResponse
        if stream_response.HasField('task'):
            task = stream_response.task
            if task.status.state == 3:  # TASK_STATE_COMPLETED
                metadata = dict(task.status.message.metadata)
                assert metadata.get('response_key') == 'response_value', (
                    f'Missing response metadata: {metadata}'
                )
        elif stream_response.HasField('status_update'):
            status_update = stream_response.status_update
            if status_update.status.state == 3:  # TASK_STATE_COMPLETED
                metadata = dict(status_update.status.message.metadata)
                assert metadata.get('response_key') == 'response_value', (
                    f'Missing response metadata: {metadata}'
                )
        break

    print(f'Success: send_message (synchronous) passed.')


async def test_get_task(client, task_id):
    print(f'Testing get_task ({task_id})...')
    task = await client.get_task(request=GetTaskRequest(id=task_id))
    assert task.id == task_id
    print('Success: get_task passed.')


async def test_cancel_task(client, task_id):
    print(f'Testing cancel_task ({task_id})...')
    await client.cancel_task(request=CancelTaskRequest(id=task_id))
    print('Success: cancel_task passed.')


async def test_subscribe(client, task_id):
    print(f'Testing subscribe ({task_id})...')
    async for event in client.subscribe(
        request=SubscribeToTaskRequest(id=task_id)
    ):
        print(f'Received event: {event}')
        break
    print('Success: subscribe passed.')


async def test_get_extended_agent_card(client):
    print('Testing get_extended_agent_card...')
    card = await client.get_extended_agent_card(
        request=GetExtendedAgentCardRequest()
    )
    assert card is not None
    print(f'Success: get_extended_agent_card passed.')


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

    client = await ClientFactory.connect(url, client_config=config)

    # 1. Get Extended Agent Card
    await test_get_extended_agent_card(client)

    # 2. Send Streaming Message
    task_id = await test_send_message_stream(client)

    # 3. Get Task
    await test_get_task(client, task_id)

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
