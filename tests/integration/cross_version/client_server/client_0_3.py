import argparse
import asyncio
import grpc
import httpx
import json
from uuid import uuid4

from a2a.client import ClientFactory, ClientConfig
from a2a.types import (
    Message,
    Part,
    Role,
    TextPart,
    TransportProtocol,
    TaskQueryParams,
    TaskIdParams,
    TaskState,
    TaskPushNotificationConfig,
    PushNotificationConfig,
    FilePart,
    FileWithUri,
    FileWithBytes,
    DataPart,
)
from a2a.client.errors import A2AClientJSONRPCError, A2AClientHTTPError
import sys
import traceback


async def test_send_message_stream(client):
    print('Testing send_message (streaming)...')

    msg = Message(
        role=Role.user,
        message_id=f'stream-{uuid4()}',
        parts=[
            Part(root=TextPart(text='stream')),
            Part(
                root=FilePart(
                    file=FileWithUri(
                        uri='https://example.com/file.txt',
                        mime_type='text/plain',
                    )
                )
            ),
            Part(
                root=FilePart(
                    file=FileWithBytes(
                        bytes=b'aGVsbG8=', mime_type='application/octet-stream'
                    )
                )
            ),
            Part(root=DataPart(data={'key': 'value'})),
        ],
        metadata={'test_key': 'full_message'},
    )
    events = []

    async for event in client.send_message(request=msg):
        events.append(event)
        break

    assert len(events) > 0, 'Expected at least one event'
    first_event = events[0]

    event_obj = (
        first_event[0] if isinstance(first_event, tuple) else first_event
    )
    task_id = getattr(event_obj, 'id', None) or getattr(
        event_obj, 'task_id', 'unknown'
    )

    print(f'Success: send_message (streaming) passed. Task ID: {task_id}')
    return task_id


async def test_send_message_sync(url, protocol_enum):
    print('Testing send_message (synchronous)...')
    config = ClientConfig()
    config.httpx_client = httpx.AsyncClient(timeout=30.0)
    config.grpc_channel_factory = grpc.aio.insecure_channel
    config.supported_transports = [protocol_enum]
    config.streaming = False

    client = await ClientFactory.connect(url, client_config=config)
    msg = Message(
        role=Role.user,
        message_id=f'sync-{uuid4()}',
        parts=[Part(root=TextPart(text='sync'))],
        metadata={'test_key': 'simple_message'},
    )

    async for event in client.send_message(request=msg):
        assert event is not None
        event_obj = event[0] if isinstance(event, tuple) else event

        status = getattr(event_obj, 'status', None)
        if status and str(getattr(status, 'state', '')).endswith('completed'):
            # In 0.3 SDK, the message on the status might be exposed as 'message' or 'update'
            status_msg = getattr(
                status, 'message', getattr(status, 'update', None)
            )
            assert status_msg is not None, (
                'TaskStatus message/update is missing'
            )

            metadata = getattr(status_msg, 'metadata', {})
            assert metadata.get('response_key') == 'response_value', (
                f'Missing response metadata: {metadata}'
            )

            # Check Part translation (root text part in 0.3)
            parts = getattr(
                status_msg, 'parts', getattr(status_msg, 'content', [])
            )
            assert len(parts) > 0, 'No parts found in TaskStatus message'
            first_part = parts[0]
            text = getattr(first_part, 'text', '')
            if (
                not text
                and hasattr(first_part, 'root')
                and hasattr(first_part.root, 'text')
            ):
                text = first_part.root.text
            assert text == 'done', f"Expected 'done' text in Part, got '{text}'"
            break

    print(f'Success: send_message (synchronous) passed.')


async def test_get_task(client, task_id):
    print(f'Testing get_task ({task_id})...')
    task = await client.get_task(request=TaskQueryParams(id=task_id))
    assert task.id == task_id

    user_msgs = [
        m for m in task.history if getattr(m, 'role', None) == Role.user
    ]
    assert user_msgs, 'Expected at least one ROLE_USER message in task history'

    client_msg = user_msgs[0]

    parts = client_msg.parts
    assert len(parts) == 4, f'Expected 4 parts, got {len(parts)}'

    # 1. text part
    text = getattr(parts[0].root, 'text', '')
    assert text == 'stream', f"Expected 'stream', got {text}"

    # 2. uri part
    file_uri = getattr(parts[1].root, 'file', None)
    assert (
        file_uri is not None
        and getattr(file_uri, 'uri', None) == 'https://example.com/file.txt'
    )

    # 3. bytes part
    file_bytes = getattr(parts[2].root, 'file', None)
    actual_bytes = getattr(file_bytes, 'bytes', None)
    assert actual_bytes == 'aGVsbG8=', (
        f"Expected base64 'hello', got {actual_bytes}"
    )

    # 4. data part
    data_val = getattr(parts[3].root, 'data', None)
    assert data_val is not None
    assert data_val == {'key': 'value'}

    print('Success: get_task passed.')


async def test_cancel_task(client, task_id):
    print(f'Testing cancel_task ({task_id})...')
    await client.cancel_task(request=TaskIdParams(id=task_id))
    task = await client.get_task(request=TaskQueryParams(id=task_id))
    assert task.status.state == TaskState.canceled, (
        f'Expected a canceled state, got {task.status.state}'
    )
    print('Success: cancel_task passed.')


async def test_subscribe(client, task_id):
    print(f'Testing subscribe ({task_id})...')
    has_artifact = False
    async for event in client.resubscribe(request=TaskIdParams(id=task_id)):
        # event is tuple (Task, UpdateEvent)
        task, update = event
        if update and hasattr(update, 'artifact'):
            has_artifact = True
            artifact = update.artifact
            assert artifact.name == 'test-artifact'
            assert artifact.metadata.get('artifact_key') == 'artifact_value'
            # part check
            assert len(artifact.parts) > 0
            p = artifact.parts[0]
            text = getattr(p.root, 'text', '')
            assert text == 'artifact-chunk'
            print('Success: received artifact update.')

        if has_artifact:
            break
    print('Success: subscribe passed.')


async def test_get_extended_agent_card(client):
    print('Testing get_extended_agent_card...')
    # In v0.3, extended card is fetched via get_card() on the client
    card = await client.get_card()
    assert card is not None
    assert card.name in ('Server 0.3', 'Server 1.0')
    assert card.version == '1.0.0'
    assert 'Server running on a2a v' in card.description

    assert card.capabilities is not None
    assert card.capabilities.streaming is True
    assert card.capabilities.push_notifications is True

    if card.name == 'Server 0.3':
        assert card.url is not None
        assert card.preferred_transport == TransportProtocol.jsonrpc
        assert len(card.additional_interfaces) == 2
        assert card.supports_authenticated_extended_card is False
    else:
        assert card.url is not None
        assert card.preferred_transport is not None
        print(
            f'card.supports_authenticated_extended_card is: {card.supports_authenticated_extended_card}'
        )
        assert card.supports_authenticated_extended_card in (False, None)

    print(f'Success: get_extended_agent_card passed.')


async def run_client(url: str, protocol: str):
    protocol_enum_map = {
        'jsonrpc': TransportProtocol.jsonrpc,
        'rest': TransportProtocol.http_json,
        'grpc': TransportProtocol.grpc,
    }
    protocol_enum = protocol_enum_map[protocol]

    config = ClientConfig()
    config.httpx_client = httpx.AsyncClient(timeout=30.0)
    config.grpc_channel_factory = grpc.aio.insecure_channel
    config.supported_transports = [protocol_enum]
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
    print('Starting client_0_3...')

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
            traceback.print_exc()
            print(f'FAILED protocol {protocol}: {e}')
            failed = True

    if failed:
        sys.exit(1)


if __name__ == '__main__':
    main()
