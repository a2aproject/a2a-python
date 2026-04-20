import argparse
import asyncio
import contextlib
import logging

import grpc
import uvicorn

from fastapi import FastAPI

from a2a.compat.v0_3 import a2a_v0_3_pb2_grpc
from a2a.compat.v0_3.grpc_handler import CompatGrpcHandler
from a2a.server.agent_execution.agent_executor import AgentExecutor
from a2a.server.agent_execution.context import RequestContext
from a2a.server.events.event_queue import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler, GrpcHandler
from a2a.server.routes import (
    create_agent_card_routes,
    create_jsonrpc_routes,
    create_rest_routes,
)
from a2a.server.tasks.inmemory_task_store import InMemoryTaskStore
from a2a.server.tasks.task_updater import TaskUpdater
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentProvider,
    AgentSkill,
    Part,
    Task,
    TaskState,
    TaskStatus,
    a2a_pb2_grpc,
)


logger = logging.getLogger(__name__)


class SampleAgentExecutor(AgentExecutor):
    """Sample agent executor logic similar to the a2a-js sample."""

    def __init__(self) -> None:
        self.running_tasks: set[str] = set()

    async def cancel(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        """Cancels a task."""
        task_id = context.task_id
        if task_id in self.running_tasks:
            self.running_tasks.remove(task_id)

        updater = TaskUpdater(
            event_queue=event_queue,
            task_id=task_id or '',
            context_id=context.context_id or '',
        )
        await updater.cancel()

    async def execute(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        """Executes a task inline."""
        user_message = context.message
        task_id = context.task_id
        context_id = context.context_id

        if not user_message or not task_id or not context_id:
            return

        self.running_tasks.add(task_id)

        logger.info(
            '[SampleAgentExecutor] Processing message %s for task %s (context: %s)',
            user_message.message_id,
            task_id,
            context_id,
        )

        await event_queue.enqueue_event(
            Task(
                id=task_id,
                context_id=context_id,
                status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
                history=[user_message],
            )
        )

        updater = TaskUpdater(
            event_queue=event_queue,
            task_id=task_id,
            context_id=context_id,
        )

        working_message = updater.new_agent_message(
            parts=[Part(text='Processing your question...')]
        )
        await updater.start_work(message=working_message)

        query = context.get_user_input()

        agent_reply_text = self._parse_input(query)
        await asyncio.sleep(1)

        if task_id not in self.running_tasks:
            return

        await updater.add_artifact(
            parts=[Part(text=agent_reply_text)],
            name='response',
            last_chunk=True,
        )
        await updater.complete()

        logger.info(
            '[SampleAgentExecutor] Task %s finished with state: completed',
            task_id,
        )

    def _parse_input(self, query: str) -> str:
        if not query:
            return 'Hello! Please provide a message for me to respond to.'

        ql = query.lower()
        if 'hello' in ql or 'hi' in ql:
            return 'Hello World! Nice to meet you!'
        if 'how are you' in ql:
            return (
                "I'm doing great! Thanks for asking. How can I help you today?"
            )
        if 'goodbye' in ql or 'bye' in ql:
            return 'Goodbye! Have a wonderful day!'
        return f"Hello World! You said: '{query}'. Thanks for your message!"


async def serve(
    host: str = '127.0.0.1',
    port: int = 41241,
    grpc_port: int = 50051,
    compat_grpc_port: int = 50052,
) -> None:
    """Run the Sample Agent server with mounted JSON-RPC, HTTP+JSON and gRPC transports."""
    agent_card = AgentCard(
        name='Sample Agent',
        description='A sample agent to test the stream functionality.',
        provider=AgentProvider(
            organization='A2A Samples', url='https://example.com'
        ),
        version='1.0.0',
        capabilities=AgentCapabilities(
            streaming=True, push_notifications=False
        ),
        default_input_modes=['text'],
        default_output_modes=['text', 'task-status'],
        skills=[
            AgentSkill(
                id='sample_agent',
                name='Sample Agent',
                description='Say hi.',
                tags=['sample'],
                examples=['hi'],
                input_modes=['text'],
                output_modes=['text', 'task-status'],
            )
        ],
        supported_interfaces=[
            AgentInterface(
                protocol_binding='GRPC',
                protocol_version='1.0',
                url=f'{host}:{grpc_port}',
            ),
            AgentInterface(
                protocol_binding='GRPC',
                protocol_version='0.3',
                url=f'{host}:{compat_grpc_port}',
            ),
            AgentInterface(
                protocol_binding='JSONRPC',
                protocol_version='1.0',
                url=f'http://{host}:{port}/a2a/jsonrpc',
            ),
            AgentInterface(
                protocol_binding='JSONRPC',
                protocol_version='0.3',
                url=f'http://{host}:{port}/a2a/jsonrpc',
            ),
            AgentInterface(
                protocol_binding='HTTP+JSON',
                protocol_version='1.0',
                url=f'http://{host}:{port}/a2a/rest',
            ),
            AgentInterface(
                protocol_binding='HTTP+JSON',
                protocol_version='0.3',
                url=f'http://{host}:{port}/a2a/rest',
            ),
        ],
    )

    task_store = InMemoryTaskStore()
    request_handler = DefaultRequestHandler(
        agent_executor=SampleAgentExecutor(),
        task_store=task_store,
        agent_card=agent_card,
    )

    rest_routes = create_rest_routes(
        request_handler=request_handler,
        path_prefix='/a2a/rest',
        enable_v0_3_compat=True,
    )
    jsonrpc_routes = create_jsonrpc_routes(
        request_handler=request_handler,
        rpc_url='/a2a/jsonrpc',
        enable_v0_3_compat=True,
    )
    agent_card_routes = create_agent_card_routes(
        agent_card=agent_card,
    )
    app = FastAPI()
    app.routes.extend(jsonrpc_routes)
    app.routes.extend(agent_card_routes)
    app.routes.extend(rest_routes)

    grpc_server = grpc.aio.server()
    grpc_server.add_insecure_port(f'{host}:{grpc_port}')
    servicer = GrpcHandler(request_handler)
    a2a_pb2_grpc.add_A2AServiceServicer_to_server(servicer, grpc_server)

    compat_grpc_server = grpc.aio.server()
    compat_grpc_server.add_insecure_port(f'{host}:{compat_grpc_port}')
    compat_servicer = CompatGrpcHandler(request_handler)
    a2a_v0_3_pb2_grpc.add_A2AServiceServicer_to_server(
        compat_servicer, compat_grpc_server
    )

    config = uvicorn.Config(app, host=host, port=port)
    uvicorn_server = uvicorn.Server(config)

    logger.info('Starting Sample Agent servers:')
    logger.info(' - HTTP on http://%s:%s', host, port)
    logger.info(' - gRPC on %s:%s', host, grpc_port)
    logger.info(' - gRPC (v0.3 compat) on %s:%s', host, compat_grpc_port)
    logger.info(
        'Agent Card available at http://%s:%s/.well-known/agent-card.json',
        host,
        port,
    )

    await asyncio.gather(
        grpc_server.start(),
        compat_grpc_server.start(),
        uvicorn_server.serve(),
    )


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description='Sample A2A agent server')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=41241)
    parser.add_argument('--grpc-port', type=int, default=50051)
    parser.add_argument('--compat-grpc-port', type=int, default=50052)
    args = parser.parse_args()
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(
            serve(
                host=args.host,
                port=args.port,
                grpc_port=args.grpc_port,
                compat_grpc_port=args.compat_grpc_port,
            )
        )
