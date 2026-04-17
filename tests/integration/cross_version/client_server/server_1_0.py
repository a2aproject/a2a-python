import argparse
import uvicorn
from fastapi import FastAPI
import asyncio
import grpc

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.routes.rest_routes import create_rest_routes
from a2a.server.events import EventQueue
from a2a.server.events.in_memory_queue_manager import InMemoryQueueManager
from a2a.server.request_handlers import DefaultRequestHandler, GrpcHandler
from a2a.server.tasks import TaskUpdater
from a2a.server.tasks.inmemory_push_notification_config_store import (
    InMemoryPushNotificationConfigStore,
)
from a2a.server.tasks.inmemory_task_store import InMemoryTaskStore
from a2a.types.a2a_pb2 import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    Part,
    TaskState,
)
from a2a.types import a2a_pb2_grpc
from a2a.compat.v0_3 import a2a_v0_3_pb2_grpc
from a2a.compat.v0_3.grpc_handler import CompatGrpcHandler
from a2a.utils import TransportProtocol
from server_common import CustomLoggingMiddleware
from google.protobuf.struct_pb2 import Struct, Value
from a2a.helpers.proto_helpers import new_task_from_user_message


class MockAgentExecutor(AgentExecutor):
    def __init__(self):
        self.events = {}

    async def execute(self, context: RequestContext, event_queue: EventQueue):
        print(f'SERVER: execute called for task {context.task_id}')
        task = new_task_from_user_message(context.message)
        task.id = context.task_id
        task.context_id = context.context_id
        task.status.state = TaskState.TASK_STATE_WORKING
        await event_queue.enqueue_event(task)

        task_updater = TaskUpdater(
            event_queue,
            context.task_id,
            context.context_id,
        )
        await task_updater.update_status(TaskState.TASK_STATE_WORKING)

        text = ''
        if context.message and context.message.parts:
            text = context.message.parts[0].text

        metadata = (
            dict(context.message.metadata)
            if context.message and context.message.metadata
            else {}
        )
        if metadata.get('test_key') not in ('full_message', 'simple_message'):
            print(f'SERVER: WARNING: Missing or incorrect metadata: {metadata}')
            raise ValueError(
                f'Missing expected metadata from client. Got: {metadata}'
            )

        for part in context.message.parts:
            if part.HasField('raw'):
                assert part.raw == b'hello'

        if metadata.get('test_key') == 'full_message':
            s = Struct()
            s.update({'key': 'value'})

            expected_parts = [
                Part(text='stream'),
                Part(
                    url='https://example.com/file.txt', media_type='text/plain'
                ),
                Part(raw=b'hello', media_type='application/octet-stream'),
                Part(data=Value(struct_value=s)),
            ]
            assert context.message.parts == expected_parts

        if 'stream' in text:
            print(f'SERVER: waiting on stream event for task {context.task_id}')
            event = asyncio.Event()
            self.events[context.task_id] = event

            async def emit_periodic():
                try:
                    while not event.is_set():
                        await task_updater.update_status(
                            TaskState.TASK_STATE_WORKING,
                            message=task_updater.new_agent_message(
                                [Part(text='ping')]
                            ),
                        )
                        await task_updater.add_artifact(
                            [Part(text='artifact-chunk')],
                            name='test-artifact',
                            metadata={'artifact_key': 'artifact_value'},
                        )
                        await asyncio.sleep(0.1)
                except asyncio.CancelledError:
                    pass

            bg_task = asyncio.create_task(emit_periodic())
            await event.wait()
            bg_task.cancel()
            print(f'SERVER: stream event triggered for task {context.task_id}')

        await task_updater.update_status(
            TaskState.TASK_STATE_COMPLETED,
            message=task_updater.new_agent_message(
                [Part(text='done')], metadata={'response_key': 'response_value'}
            ),
        )
        print(f'SERVER: execute finished for task {context.task_id}')

    async def cancel(self, context: RequestContext, event_queue: EventQueue):
        print(f'SERVER: cancel called for task {context.task_id}')
        assert context.task_id in self.events
        self.events[context.task_id].set()
        task_updater = TaskUpdater(
            event_queue,
            context.task_id,
            context.context_id,
        )
        await task_updater.update_status(TaskState.TASK_STATE_CANCELED)


async def main_async(http_port: int, grpc_port: int):
    agent_card = AgentCard(
        name='Server 1.0',
        description='Server running on a2a v1.0',
        version='1.0.0',
        skills=[],
        capabilities=AgentCapabilities(streaming=True, push_notifications=True),
        default_input_modes=['text/plain'],
        default_output_modes=['text/plain'],
        supported_interfaces=[
            AgentInterface(
                protocol_binding=TransportProtocol.JSONRPC,
                url=f'http://127.0.0.1:{http_port}/jsonrpc/',
            ),
            AgentInterface(
                protocol_binding=TransportProtocol.HTTP_JSON,
                url=f'http://127.0.0.1:{http_port}/rest/',
                protocol_version='1.0',
            ),
            AgentInterface(
                protocol_binding=TransportProtocol.HTTP_JSON,
                url=f'http://127.0.0.1:{http_port}/rest/',
                protocol_version='0.3',
            ),
            AgentInterface(
                protocol_binding=TransportProtocol.GRPC,
                url=f'127.0.0.1:{grpc_port}',
            ),
        ],
    )

    task_store = InMemoryTaskStore()
    handler = DefaultRequestHandler(
        MockAgentExecutor(),
        task_store,
        agent_card,
        queue_manager=InMemoryQueueManager(),
        push_config_store=InMemoryPushNotificationConfigStore(),
        extended_agent_card=agent_card,
    )

    app = FastAPI()
    app.add_middleware(CustomLoggingMiddleware)

    agent_card_routes = create_agent_card_routes(
        agent_card=agent_card, card_url='/.well-known/agent-card.json'
    )
    jsonrpc_routes = create_jsonrpc_routes(
        request_handler=handler,
        rpc_url='/',
        enable_v0_3_compat=True,
    )
    app.mount(
        '/jsonrpc',
        FastAPI(routes=jsonrpc_routes + agent_card_routes),
    )

    rest_routes = create_rest_routes(
        request_handler=handler,
        enable_v0_3_compat=True,
    )
    app.mount(
        '/rest',
        FastAPI(routes=rest_routes + agent_card_routes),
    )

    # Start gRPC Server
    server = grpc.aio.server()
    servicer = GrpcHandler(handler)
    a2a_pb2_grpc.add_A2AServiceServicer_to_server(servicer, server)

    compat_servicer = CompatGrpcHandler(handler)
    a2a_v0_3_pb2_grpc.add_A2AServiceServicer_to_server(compat_servicer, server)

    server.add_insecure_port(f'127.0.0.1:{grpc_port}')
    await server.start()

    # Start Uvicorn
    config = uvicorn.Config(
        app, host='127.0.0.1', port=http_port, log_level='info', access_log=True
    )
    uvicorn_server = uvicorn.Server(config)
    await uvicorn_server.serve()


def main():
    print('Starting server_1_0...')

    parser = argparse.ArgumentParser()
    parser.add_argument('--http-port', type=int, required=True)
    parser.add_argument('--grpc-port', type=int, required=True)
    args = parser.parse_args()

    asyncio.run(main_async(args.http_port, args.grpc_port))


if __name__ == '__main__':
    main()
