import argparse
import asyncio
import grpc
import httpx
from uuid import uuid4

from a2a.client import ClientFactory, ClientConfig
from a2a.types.a2a_pb2 import (
    Message,
    Part,
    Role,
    SendMessageRequest,
    GetTaskRequest,
    CancelTaskRequest,
    SubscribeToTaskRequest,
    TaskPushNotificationConfig,
    PushNotificationConfig,
    CreateTaskPushNotificationConfigRequest,
    GetExtendedAgentCardRequest,
    TaskState,
)
from a2a.utils import TransportProtocol
from a2a.client.errors import A2AClientJSONRPCError, A2AClientHTTPError

async def test_send_message_stream(client):
    print("Testing send_message (streaming)...")
    msg = Message(
        role=Role.ROLE_USER,
        message_id=f"stream-{uuid4()}",
        parts=[Part(text='stream')]
    )
    msg.metadata.update({"test_key": "test_value"})
    events = []
    task_id = None
    async for event_tuple in client.send_message(request=msg):
        # event_tuple is (StreamResponse, Task | None)
        events.append(event_tuple)
        stream_res, task = event_tuple
        if task and task.id:
             task_id = task.id
             break # Break early to keep task active
        if stream_res.HasField('task') and stream_res.task.id:
             task_id = stream_res.task.id
             break
        if stream_res.HasField('message') and stream_res.message.task_id:
             task_id = stream_res.message.task_id
             break
             
    assert task_id is not None, "Failed to get task_id from stream"
    print(f"Success: send_message (streaming) passed. Task ID: {task_id}")
    return task_id

async def test_send_message_sync(url, protocol_enum):
    print("Testing send_message (synchronous)...")
    config = ClientConfig()
    config.httpx_client=httpx.AsyncClient(timeout=30.0)
    config.grpc_channel_factory=grpc.aio.insecure_channel
    config.supported_protocol_bindings=[protocol_enum]
    config.streaming=False
    
    client = await ClientFactory.connect(url, client_config=config)
    msg = Message(
        role=Role.ROLE_USER,
        message_id=f"sync-{uuid4()}",
        parts=[Part(text='sync')]
    )
    # Inject metadata dictionary for v1.0 PB
    msg.metadata.update({"test_key": "test_value"})
    
    async for event_tuple in client.send_message(request=msg):
         assert event_tuple is not None
         stream_res, task = event_tuple
         
         # The final event should have a task with the expected metadata
         if task and task.status.state == TaskState.TASK_STATE_COMPLETED:
             metadata = task.status.message.metadata
             assert "response_key" in metadata and metadata["response_key"] == "response_value", f"Missing response metadata: {metadata}"
         break
    print("Success: send_message (synchronous) passed.")

async def test_get_task(client, task_id):
    print(f"Testing get_task ({task_id})...")
    task = await client.get_task(request=GetTaskRequest(id=task_id))
    assert task.id == task_id
    print("Success: get_task passed.")

async def test_cancel_task(client, task_id):
    print(f"Testing cancel_task ({task_id})...")
    await client.cancel_task(request=CancelTaskRequest(id=task_id))
    print("Success: cancel_task passed.")

async def test_subscribe(client, task_id):
    print(f"Testing subscribe ({task_id})...")
    async for event in client.subscribe(request=SubscribeToTaskRequest(id=task_id)):
         print(f"Received event: {event}")
         break
    print("Success: subscribe passed.")

async def test_push_config(client, task_id):
    print(f"Testing push_config ({task_id})...")
    config = PushNotificationConfig(url="http://example.com/webhook")
    try:
        await client.set_task_callback(
            request=CreateTaskPushNotificationConfigRequest(task_id=task_id, config=config)
        )
        print("Success: push_config passed.")
    except (A2AClientJSONRPCError, A2AClientHTTPError, grpc.aio.AioRpcError, httpx.HTTPStatusError, NotImplementedError) as e:
        print(f"Note: push_config failed as expected (Not Implemented): {type(e).__name__} - {e}")

async def test_get_extended_agent_card(client):
    print("Testing get_extended_agent_card...")
    try:
        card = await client.get_extended_agent_card()
        assert card is not None
        print(f"Success: get_extended_agent_card passed. Card name: {card.name}")
    except (A2AClientJSONRPCError, A2AClientHTTPError, grpc.aio.AioRpcError, httpx.HTTPStatusError) as e:
        print(f"Note: get_extended_agent_card failed as expected (not implemented by mock): {type(e).__name__} - {e}")

async def test_list_tasks(client):
    from a2a.types.a2a_pb2 import ListTasksRequest
    from a2a.utils.errors import UnsupportedOperationError
    print("Testing list_tasks...")
    try:
        response = await client.list_tasks(request=ListTasksRequest())
        print(f"Success: list_tasks passed. Total size: {response.total_size}")
    except UnsupportedOperationError as e:
        print(f"Success: list_tasks passed (UnsupportedOperationError caught correctly: {e})")
    except Exception as e:
        if "not supported in v0.3" in str(e) or "Method not found" in str(e) or "Unimplemented" in str(e) or "UnsupportedOperationError" in str(type(e)):
            print(f"Success: list_tasks passed (expected lack of support for v0.3: {e})")
        else:
            print(f"Note: list_tasks failed unexpectedly: {e}")
            raise

async def run_client(url: str, protocol: str):
    protocol_enum_map = {
        'jsonrpc': TransportProtocol.JSONRPC,
        'rest': TransportProtocol.HTTP_JSON,
        'grpc': TransportProtocol.GRPC
    }
    protocol_enum = protocol_enum_map[protocol]

    config = ClientConfig()
    config.httpx_client=httpx.AsyncClient(timeout=30.0)
    config.grpc_channel_factory=grpc.aio.insecure_channel
    config.supported_protocol_bindings=[protocol_enum]
    config.streaming=True
    
    client = await ClientFactory.connect(url, client_config=config)
    
    try:
        # 1. Get Extended Agent Card
        await test_get_extended_agent_card(client)
        
        # 2. List Tasks
        await test_list_tasks(client)

        # 3. Send Streaming Message
        task_id = await test_send_message_stream(client)
        
        # 4. Get Task
        if task_id:
            await test_get_task(client, task_id)
        
        # 5. Subscribe to Task
        if task_id:
            await test_subscribe(client, task_id)
        
        # 6. Push Config
        if task_id:
            await test_push_config(client, task_id)
            
        # 7. Cancel Task
        if task_id:
            await test_cancel_task(client, task_id)
        
        # 8. Send Sync Message
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
