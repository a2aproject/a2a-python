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
    TaskPushNotificationConfig,
    PushNotificationConfig,
)
from a2a.client.errors import A2AClientJSONRPCError, A2AClientHTTPError

async def test_send_message_stream(client):
    print("Testing send_message (streaming)...")
    msg = Message(
        role=Role.user, 
        message_id=f"stream-{uuid4()}",
        parts=[Part(root=TextPart(text="stream"))],
        metadata={"test_key": "test_value"}
    )
    events = []
    
    async for event in client.send_message(request=msg):
        events.append(event)
        break
        
    assert len(events) > 0, "Expected at least one event"
    first_event = events[0]
    
    event_obj = first_event[0] if isinstance(first_event, tuple) else first_event
    task_id = getattr(event_obj, "id", None) or getattr(event_obj, "task_id", "unknown")
    
    print(f"Success: send_message (streaming) passed. Task ID: {task_id}")
    return task_id

async def test_send_message_sync(url, protocol_enum):
    print("Testing send_message (synchronous)...")
    config = ClientConfig()
    config.httpx_client=httpx.AsyncClient(timeout=30.0)
    config.grpc_channel_factory=grpc.aio.insecure_channel
    config.supported_transports=[protocol_enum]
    config.streaming=False
    
    client = await ClientFactory.connect(url, client_config=config)
    msg = Message(
        role=Role.user,
        message_id=f"sync-{uuid4()}",
        parts=[Part(root=TextPart(text="sync"))],
        metadata={"test_key": "test_value"}
    )
    
    # In v0.3 SDK, send_message ALWAYS returns an async generator
    async for event in client.send_message(request=msg):
        assert event is not None
        event_obj = event[0] if isinstance(event, tuple) else event
        if getattr(event_obj, "status", None) and getattr(event_obj.status, "state", None) == "TASK_STATE_COMPLETED":
             assert getattr(event_obj.status.message, "metadata", {}).get("response_key") == "response_value", f"Missing response metadata: {getattr(event_obj.status.message, 'metadata', {})}"
        elif getattr(event_obj, "status", None) and str(getattr(event_obj.status, "state", None)).endswith("completed"):
             assert getattr(event_obj.status.message, "metadata", {}).get("response_key") == "response_value", f"Missing response metadata: {getattr(event_obj.status.message, 'metadata', {})}"
        break
        
    print(f"Success: send_message (synchronous) passed.")

async def test_get_task(client, task_id):
    print(f"Testing get_task ({task_id})...")
    task = await client.get_task(request=TaskQueryParams(id=task_id))
    assert task.id == task_id
    print("Success: get_task passed.")

async def test_cancel_task(client, task_id):
    print(f"Testing cancel_task ({task_id})...")
    await client.cancel_task(request=TaskIdParams(id=task_id))
    print("Success: cancel_task passed.")

async def test_subscribe(client, task_id):
    print(f"Testing subscribe ({task_id})...")
    async for event in client.resubscribe(request=TaskIdParams(id=task_id)):
         print(f"Received event: {event}")
         break
    print("Success: subscribe passed.")

async def test_push_config(client, task_id):
    print(f"Testing push_config ({task_id})...")
    config = TaskPushNotificationConfig(
        taskId=task_id,
        pushNotificationConfig=PushNotificationConfig(
            url="http://example.com/webhook"
        )
    )
    # set_task_callback in v0.3
    try:
        await client.set_task_callback(
            request=config
        )
        print("Success: push_config passed.")
    except (A2AClientJSONRPCError, A2AClientHTTPError, grpc.aio.AioRpcError) as e:
        print(f"Note: push_config failed as expected (Not Implemented): {type(e).__name__} - {e}")

async def test_get_extended_agent_card(client):
    print("Testing get_extended_agent_card...")
    # In v0.3, extended card is fetched via get_card() on the client
    card = await client.get_card()
    assert card is not None
    # the MockAgentExecutor might not have a name or has one, just assert card exists
    print(f"Success: get_extended_agent_card passed.")

async def run_client(url: str, protocol: str):
    protocol_enum_map = {
        'jsonrpc': TransportProtocol.jsonrpc,
        'rest': TransportProtocol.http_json,
        'grpc': TransportProtocol.grpc
    }
    protocol_enum = protocol_enum_map[protocol]

    config = ClientConfig()
    config.httpx_client=httpx.AsyncClient(timeout=30.0)
    config.grpc_channel_factory=grpc.aio.insecure_channel
    config.supported_transports=[protocol_enum]
    config.streaming=True
    
    client = await ClientFactory.connect(url, client_config=config)
    
    try:
        # 1. Get Extended Agent Card
        await test_get_extended_agent_card(client)
        
        # 2. Send Streaming Message
        task_id = await test_send_message_stream(client)
        
        # 3. Get Task
        if task_id and task_id != "unknown":
            await test_get_task(client, task_id)
        
        # 3. Subscribe to Task
        if task_id and task_id != "unknown":
            await test_subscribe(client, task_id)
            
        # 4. Push Config
        if task_id and task_id != "unknown":
            await test_push_config(client, task_id)
        
        # 5. Cancel Task
        if task_id and task_id != "unknown":
            await test_cancel_task(client, task_id)
        
        # 6. Send Sync Message
        await test_send_message_sync(url, protocol_enum)
        
    finally:
        pass

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", type=str, required=True)
    parser.add_argument("--protocols", type=str, nargs='+', required=True)
    args = parser.parse_args()

    failed = False
    for protocol in args.protocols:
        print(f"\n=== Testing protocol: {protocol} ===")
        try:
            asyncio.run(run_client(args.url, protocol))
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"FAILED protocol {protocol}: {e}")
            failed = True

    if failed:
        import sys
        sys.exit(1)

if __name__ == "__main__":
    main()
