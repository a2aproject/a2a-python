import asyncio
import logging
import os
import uuid

from datetime import datetime, timezone

import grpc.aio
import uvicorn

from starlette.applications import Starlette

import a2a.compat.v0_3.a2a_v0_3_pb2_grpc as a2a_v0_3_grpc
import a2a.types.a2a_pb2_grpc as a2a_grpc

from a2a.compat.v0_3.grpc_handler import CompatGrpcHandler
from a2a.server.agent_execution.agent_executor import AgentExecutor
from a2a.server.agent_execution.context import RequestContext
from a2a.server.events.event_queue import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.request_handlers.grpc_handler import GrpcHandler
from a2a.server.routes import (
    create_agent_card_routes,
    create_jsonrpc_routes,
    create_rest_routes,
)
from a2a.server.tasks.inmemory_task_store import InMemoryTaskStore
from a2a.server.tasks.task_store import TaskStore
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentProvider,
    AgentSkill,
    Message,
    Part,
    Role,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)


JSONRPC_URL = '/a2a/jsonrpc'
REST_URL = '/a2a/rest'

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('SUTAgent')


class SUTAgentExecutor(AgentExecutor):
    """Execution logic for the SUT agent."""

    def __init__(self) -> None:
        """Initializes the SUT agent executor."""
        self.running_tasks: set[str] = set()

    async def cancel(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        """Cancels a task."""
        api_task_id = context.task_id
        if api_task_id is None:
            return
        if api_task_id in self.running_tasks:
            self.running_tasks.remove(api_task_id)

        status_update = TaskStatusUpdateEvent(
            task_id=api_task_id,
            context_id=context.context_id or str(uuid.uuid4()),
            status=TaskStatus(
                state=TaskState.TASK_STATE_CANCELED,
                timestamp=datetime.now(timezone.utc),
            ),
        )
        await event_queue.enqueue_event(status_update)

    async def execute(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        """Executes a task."""
        user_message = context.message
        task_id = context.task_id
        if user_message is None or task_id is None:
            return
        context_id = context.context_id

        self.running_tasks.add(task_id)

        logger.info(
            '[SUTAgentExecutor] Processing message %s for task %s (context: %s)',
            user_message.message_id,
            task_id,
            context_id,
        )

        working_status = TaskStatusUpdateEvent(
            task_id=task_id,
            context_id=context_id,
            status=TaskStatus(
                state=TaskState.TASK_STATE_WORKING,
                message=Message(
                    role=Role.ROLE_AGENT,
                    message_id=str(uuid.uuid4()),
                    parts=[Part(text='Processing your question')],
                    task_id=task_id,
                    context_id=context_id,
                ),
                timestamp=datetime.now(timezone.utc),
            ),
        )
        await event_queue.enqueue_event(working_status)

        agent_reply_text = 'Hello world!'
        await asyncio.sleep(3)  # Simulate processing delay

        if task_id not in self.running_tasks:
            logger.info('Task %s was cancelled.', task_id)
            return

        logger.info('[SUTAgentExecutor] Response: %s', agent_reply_text)

        agent_message = Message(
            role=Role.ROLE_AGENT,
            message_id=str(uuid.uuid4()),
            parts=[Part(text=agent_reply_text)],
            task_id=task_id,
            context_id=context_id,
        )

        final_update = TaskStatusUpdateEvent(
            task_id=task_id,
            context_id=context_id,
            status=TaskStatus(
                state=TaskState.TASK_STATE_INPUT_REQUIRED,
                message=agent_message,
                timestamp=datetime.now(timezone.utc),
            ),
        )
        await event_queue.enqueue_event(final_update)


def serve(task_store: TaskStore) -> None:
    """Sets up the A2A service and starts the HTTP server."""
    http_port = int(os.environ.get('HTTP_PORT', '41241'))

    grpc_port = int(os.environ.get('GRPC_PORT', '50051'))

    agent_card = AgentCard(
        name='SUT Agent',
        description='An agent to be used as SUT against TCK tests.',
        supported_interfaces=[
            AgentInterface(
                url=f'http://localhost:{http_port}{JSONRPC_URL}',
                protocol_binding='JSONRPC',
                protocol_version='1.0.0',
            ),
            AgentInterface(
                url=f'http://localhost:{http_port}{REST_URL}',
                protocol_binding='REST',
                protocol_version='1.0.0',
            ),
            AgentInterface(
                url=f'http://localhost:{grpc_port}',
                protocol_binding='GRPC',
                protocol_version='1.0.0',
            ),
        ],
        provider=AgentProvider(
            organization='A2A Samples',
            url='https://example.com/a2a-samples',
        ),
        version='1.0.0',
        capabilities=AgentCapabilities(
            streaming=True,
            push_notifications=False,
        ),
        default_input_modes=['text'],
        default_output_modes=['text', 'task-status'],
        skills=[
            AgentSkill(
                id='sut_agent',
                name='SUT Agent',
                description='Simulate the general flow of a streaming agent.',
                tags=['sut'],
                examples=['hi', 'hello world', 'how are you', 'goodbye'],
                input_modes=['text'],
                output_modes=['text', 'task-status'],
            )
        ],
    )

    request_handler = DefaultRequestHandler(
        agent_card=agent_card,
        agent_executor=SUTAgentExecutor(),
        task_store=task_store,
    )

    # JSONRPC
    jsonrpc_routes = create_jsonrpc_routes(
        request_handler=request_handler,
        rpc_url=JSONRPC_URL,
    )
    # Agent Card
    agent_card_routes = create_agent_card_routes(
        agent_card=agent_card,
    )
    # REST
    rest_routes = create_rest_routes(
        request_handler=request_handler,
        path_prefix=REST_URL,
    )

    routes = [
        *jsonrpc_routes,
        *agent_card_routes,
        *rest_routes,
    ]
    main_app = Starlette(routes=routes)

    config = uvicorn.Config(
        main_app, host='127.0.0.1', port=http_port, log_level='info'
    )
    uvicorn_server = uvicorn.Server(config)

    # GRPC
    grpc_server = grpc.aio.server()
    grpc_server.add_insecure_port(f'[::]:{grpc_port}')
    servicer = GrpcHandler(request_handler)
    compat_servicer = CompatGrpcHandler(request_handler)
    a2a_grpc.add_A2AServiceServicer_to_server(servicer, grpc_server)
    a2a_v0_3_grpc.add_A2AServiceServicer_to_server(compat_servicer, grpc_server)

    logger.info(
        'Starting HTTP server on port %s and gRPC on port %s...',
        http_port,
        grpc_port,
    )

    loop = asyncio.get_event_loop()
    loop.run_until_complete(grpc_server.start())
    loop.run_until_complete(
        asyncio.gather(
            uvicorn_server.serve(), grpc_server.wait_for_termination()
        )
    )


def main() -> None:
    """Main entrypoint."""
    serve(InMemoryTaskStore())


if __name__ == '__main__':
    main()
