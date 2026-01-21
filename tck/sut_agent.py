import asyncio
import logging
import os
import uuid

from datetime import datetime, timezone

from uvicorn import Config, Server

from a2a.server.agent_execution.agent_executor import AgentExecutor
from a2a.server.agent_execution.context import RequestContext
from a2a.server.apps.jsonrpc.fastapi_app import A2AFastAPIApplication
from a2a.server.events.event_queue import EventQueue
from a2a.server.events.in_memory_queue_manager import InMemoryQueueManager
from a2a.server.request_handlers.default_request_handler import (
    DefaultRequestHandler,
)
from a2a.server.tasks.inmemory_task_store import InMemoryTaskStore
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentProvider,
    Message,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
    TextPart,
)


# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('SUTAgent')


class SUTAgentExecutor(AgentExecutor):
    """Execution logic for the SUT agent."""

    def __init__(self) -> None:
        """Initializes the SUT agent executor."""
        self.running_tasks = set()
        self.last_context_id = None

    async def cancel(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        """Cancels a task."""
        api_task_id = context.task_id
        if api_task_id in self.running_tasks:
            self.running_tasks.remove(api_task_id)

        status_update = TaskStatusUpdateEvent(
            task_id=api_task_id,
            context_id=self.last_context_id or str(uuid.uuid4()),
            status=TaskStatus(
                state=TaskState.canceled,
                timestamp=datetime.now(timezone.utc).isoformat(),
            ),
            final=True,
        )
        await event_queue.enqueue_event(status_update)

    async def execute(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        """Executes a task."""
        user_message = context.message
        task_id = context.task_id
        context_id = context.context_id
        self.last_context_id = context_id

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
                state=TaskState.working,
                message=Message(
                    role='agent',
                    message_id=str(uuid.uuid4()),
                    parts=[TextPart(text='Processing your question')],
                    task_id=task_id,
                    context_id=context_id,
                ),
                timestamp=datetime.now(timezone.utc).isoformat(),
            ),
            final=False,
        )
        await event_queue.enqueue_event(working_status)

        agent_reply_text = 'Hello world!'
        await asyncio.sleep(3)  # Simulate processing delay

        if task_id not in self.running_tasks:
            logger.info('Task %s was cancelled.', task_id)
            return

        logger.info('[SUTAgentExecutor] Response: %s', agent_reply_text)

        agent_message = Message(
            role='agent',
            message_id=str(uuid.uuid4()),
            parts=[TextPart(text=agent_reply_text)],
            task_id=task_id,
            context_id=context_id,
        )

        final_update = TaskStatusUpdateEvent(
            task_id=task_id,
            context_id=context_id,
            status=TaskStatus(
                state=TaskState.input_required,
                message=agent_message,
                timestamp=datetime.now(timezone.utc).isoformat(),
            ),
            final=True,
        )
        await event_queue.enqueue_event(final_update)


async def main() -> None:
    """Main entrypoint."""
    http_port = int(os.environ.get('HTTP_PORT', '41241'))

    agent_executor = SUTAgentExecutor()
    task_store = InMemoryTaskStore()
    queue_manager = InMemoryQueueManager()

    request_handler = DefaultRequestHandler(
        task_store=task_store,
        queue_manager=queue_manager,
        agent_executor=agent_executor,
    )

    sut_agent_card = AgentCard(
        name='SUT Agent',
        description='A sample agent to be used as SUT against tck tests.',
        url=f'http://localhost:{http_port}/a2a/jsonrpc',
        provider=AgentProvider(
            organization='A2A Samples',
            url='https://example.com/a2a-samples',
        ),
        version='1.0.0',
        protocol_version='0.3.0',
        capabilities=AgentCapabilities(
            streaming=True,
            push_notifications=False,
            state_transition_history=True,
        ),
        default_input_modes=['text'],
        default_output_modes=['text', 'task-status'],
        skills=[
            {
                'id': 'sut_agent',
                'name': 'SUT Agent',
                'description': 'Simulate the general flow of a streaming agent.',
                'tags': ['sut'],
                'examples': ['hi', 'hello world', 'how are you', 'goodbye'],
                'input_modes': ['text'],
                'output_modes': ['text', 'task-status'],
            }
        ],
        supports_authenticated_extended_card=False,
        preferred_transport='JSONRPC',
        additional_interfaces=[
            {
                'url': f'http://localhost:{http_port}/a2a/jsonrpc',
                'transport': 'JSONRPC',
            },
        ],
    )

    json_rpc_app = A2AFastAPIApplication(
        agent_card=sut_agent_card,
        http_handler=request_handler,
    )
    app = json_rpc_app.build(
        rpc_url='/a2a/jsonrpc', agent_card_url='/.well-known/agent-card.json'
    )

    logger.info('Starting HTTP server on port %s...', http_port)
    config = Config(app, host='127.0.0.1', port=http_port, log_level='info')
    server = Server(config)

    await server.serve()


if __name__ == '__main__':
    asyncio.run(main())
