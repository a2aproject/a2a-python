import argparse
import uvicorn
from fastapi import FastAPI
import asyncio
import grpc
import sys
import time

from a2a.server.agent_execution.agent_executor import AgentExecutor
from a2a.server.agent_execution.context import RequestContext
from a2a.server.apps.jsonrpc.fastapi_app import A2AFastAPIApplication
from a2a.server.apps.rest.fastapi_app import A2ARESTFastAPIApplication
from a2a.server.events.event_queue import EventQueue
from a2a.server.events.in_memory_queue_manager import InMemoryQueueManager
from a2a.server.request_handlers.default_request_handler import (
    DefaultRequestHandler,
)
from a2a.server.request_handlers.grpc_handler import GrpcHandler
from a2a.server.tasks.task_updater import TaskUpdater
from a2a.server.tasks.inmemory_push_notification_config_store import (
    InMemoryPushNotificationConfigStore,
)
from a2a.server.tasks.inmemory_task_store import InMemoryTaskStore
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    Part,
    TaskState,
    TextPart,
    FilePart,
    TransportProtocol,
    FileWithBytes,
    FileWithUri,
    DataPart,
)
from a2a.grpc import a2a_pb2_grpc
from starlette.requests import Request
from starlette.concurrency import iterate_in_threadpool
import time
from a2a.utils.task import new_task
from server_common import CustomLoggingMiddleware


class MockAgentExecutor(AgentExecutor):
    def __init__(self):
        self.events = {}

    async def execute(self, context: RequestContext, event_queue: EventQueue):
        print(f'SERVER: execute called for task {context.task_id}')

        task = new_task(context.message)
        task.id = context.task_id
        task.context_id = context.context_id
        task.status.state = TaskState.working
        await event_queue.enqueue_event(task)

        task_updater = TaskUpdater(
            event_queue,
            context.task_id,
            context.context_id,
        )
        await task_updater.update_status(TaskState.working)

        text = ''
        if context.message and context.message.parts:
            part = context.message.parts[0]
            if hasattr(part, 'root') and hasattr(part.root, 'text'):
                text = part.root.text
            elif hasattr(part, 'text'):
                text = part.text

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

        if metadata.get('test_key') == 'full_message':
            expected_parts = [
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
                            bytes=b'aGVsbG8=',
                            mime_type='application/octet-stream',
                        )
                    )
                ),
                Part(root=DataPart(data={'key': 'value'})),
            ]
            assert context.message.parts == expected_parts

        print(f"SERVER: request message text='{text}'")

        if 'stream' in text:
            print(f'SERVER: waiting on stream event for task {context.task_id}')
            event = asyncio.Event()
            self.events[context.task_id] = event

            async def emit_periodic():
                try:
                    while not event.is_set():
                        await task_updater.update_status(
                            TaskState.working,
                            message=task_updater.new_agent_message(
                                [Part(root=TextPart(text='ping'))]
                            ),
                        )
                        await task_updater.add_artifact(
                            [Part(root=TextPart(text='artifact-chunk'))],
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
            TaskState.completed,
            message=task_updater.new_agent_message(
                [Part(root=TextPart(text='done'))],
                metadata={'response_key': 'response_value'},
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
        await task_updater.update_status(TaskState.canceled)


async def main_async(http_port: int, grpc_port: int):
    print(
        f'SERVER: Starting server on http_port={http_port}, grpc_port={grpc_port}'
    )

    agent_card = AgentCard(
        name='Server 0.3',
        description='Server running on a2a v0.3.0',
        version='1.0.0',
        url=f'http://127.0.0.1:{http_port}/jsonrpc/',
        preferred_transport=TransportProtocol.jsonrpc,
        skills=[],
        capabilities=AgentCapabilities(streaming=True, push_notifications=True),
        default_input_modes=['text/plain'],
        default_output_modes=['text/plain'],
        additional_interfaces=[
            AgentInterface(
                transport=TransportProtocol.http_json,
                url=f'http://127.0.0.1:{http_port}/rest/',
            ),
            AgentInterface(
                transport=TransportProtocol.grpc,
                url=f'127.0.0.1:{grpc_port}',
            ),
        ],
        supports_authenticated_extended_card=False,
    )

    task_store = InMemoryTaskStore()
    handler = DefaultRequestHandler(
        agent_executor=MockAgentExecutor(),
        task_store=task_store,
        queue_manager=InMemoryQueueManager(),
        push_config_store=InMemoryPushNotificationConfigStore(),
    )

    app = FastAPI()
    app.mount(
        '/jsonrpc',
        A2AFastAPIApplication(
            http_handler=handler, agent_card=agent_card
        ).build(),
    )
    app.mount(
        '/rest',
        A2ARESTFastAPIApplication(
            http_handler=handler, agent_card=agent_card
        ).build(),
    )
    # Start gRPC Server
    server = grpc.aio.server()
    servicer = GrpcHandler(agent_card, handler)
    a2a_pb2_grpc.add_A2AServiceServicer_to_server(servicer, server)
    server.add_insecure_port(f'127.0.0.1:{grpc_port}')
    await server.start()

    app.add_middleware(CustomLoggingMiddleware)

    # Start Uvicorn
    config = uvicorn.Config(
        app, host='127.0.0.1', port=http_port, log_level='info', access_log=True
    )
    uvicorn_server = uvicorn.Server(config)
    await uvicorn_server.serve()


def main():
    print('Starting server_0_3...')

    parser = argparse.ArgumentParser()
    parser.add_argument('--http-port', type=int, required=True)
    parser.add_argument('--grpc-port', type=int, required=True)
    args = parser.parse_args()

    asyncio.run(main_async(args.http_port, args.grpc_port))


if __name__ == '__main__':
    main()
