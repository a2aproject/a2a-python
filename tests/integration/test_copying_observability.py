import httpx
import pytest
from typing import NamedTuple

from starlette.applications import Starlette

from a2a.client.client import Client, ClientConfig
from a2a.client.client_factory import ClientFactory
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.events import EventQueue
from a2a.server.events.in_memory_queue_manager import InMemoryQueueManager
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import TaskUpdater
from a2a.server.tasks.inmemory_task_store import InMemoryTaskStore
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    Artifact,
    GetTaskRequest,
    Message,
    Part,
    Role,
    SendMessageRequest,
    TaskState,
)
from a2a.helpers.proto_helpers import new_task_from_user_message
from a2a.utils import TransportProtocol


class MockMutatingAgentExecutor(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue):
        assert context.task_id is not None
        assert context.context_id is not None
        task_updater = TaskUpdater(
            event_queue,
            context.task_id,
            context.context_id,
        )

        user_input = context.get_user_input()

        if user_input == 'Init task':
            # Explicitly save status change to ensure task exists with some state
            task = new_task_from_user_message(context.message)
            task.id = context.task_id
            task.context_id = context.context_id
            task.status.state = TaskState.TASK_STATE_WORKING
            await event_queue.enqueue_event(task)

            await task_updater.update_status(
                TaskState.TASK_STATE_WORKING,
                message=task_updater.new_agent_message(
                    [Part(text='task working')]
                ),
            )
        else:
            # Mutate the task WITHOUT saving it properly
            assert context.current_task is not None
            context.current_task.artifacts.append(
                Artifact(
                    name='leaked-artifact',
                    parts=[Part(text='leaked artifact')],
                )
            )

    async def cancel(self, context: RequestContext, event_queue: EventQueue):
        raise NotImplementedError('Cancellation is not supported')


@pytest.fixture
def agent_card() -> AgentCard:
    return AgentCard(
        name='Mutating Agent',
        description='Real in-memory integration testing.',
        version='1.0.0',
        capabilities=AgentCapabilities(
            streaming=True, push_notifications=False
        ),
        skills=[],
        default_input_modes=['text/plain'],
        default_output_modes=['text/plain'],
        supported_interfaces=[
            AgentInterface(
                protocol_binding=TransportProtocol.JSONRPC,
                url='http://testserver',
            ),
        ],
    )


class ClientSetup(NamedTuple):
    client: Client
    task_store: InMemoryTaskStore
    use_copying: bool


def setup_client(agent_card: AgentCard, use_copying: bool) -> ClientSetup:
    task_store = InMemoryTaskStore(use_copying=use_copying)
    handler = DefaultRequestHandler(
        agent_executor=MockMutatingAgentExecutor(),
        task_store=task_store,
        agent_card=agent_card,
        queue_manager=InMemoryQueueManager(),
        extended_agent_card=agent_card,
    )
    agent_card_routes = create_agent_card_routes(
        agent_card=agent_card, card_url='/'
    )
    jsonrpc_routes = create_jsonrpc_routes(
        request_handler=handler,
        rpc_url='/',
    )
    app = Starlette(routes=[*agent_card_routes, *jsonrpc_routes])
    httpx_client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url='http://testserver'
    )
    factory = ClientFactory(
        config=ClientConfig(
            httpx_client=httpx_client,
            supported_protocol_bindings=[TransportProtocol.JSONRPC],
        )
    )
    client = factory.create(agent_card)
    return ClientSetup(
        client=client,
        task_store=task_store,
        use_copying=use_copying,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize('use_copying', [True, False])
async def test_mutation_observability(agent_card: AgentCard, use_copying: bool):
    """Tests that task mutations are observable when copying is disabled.

    When copying is disabled, the agent mutates the task in-place and the
    changes are observable by the client. When copying is enabled, the agent
    mutates a copy of the task and the changes are not observable by the client.

    It is ok to remove the `use_copying` parameter from the system in the future
    to make InMemoryTaskStore consistent with other task stores.
    """
    client_setup = setup_client(agent_card, use_copying)
    client = client_setup.client

    # 1. First message to create the task
    message_to_send = Message(
        role=Role.ROLE_USER,
        message_id='msg-mut-init',
        parts=[Part(text='Init task')],
    )

    events = [
        event
        async for event in client.send_message(
            request=SendMessageRequest(message=message_to_send)
        )
    ]

    event = events[-1]
    assert event.HasField('status_update')
    task_id = event.status_update.task_id

    # 2. Second message to mutate it
    message_to_send_2 = Message(
        role=Role.ROLE_USER,
        message_id='msg-mut-do',
        task_id=task_id,
        parts=[Part(text='Update task without saving it')],
    )
    _ = [
        event
        async for event in client.send_message(
            request=SendMessageRequest(message=message_to_send_2)
        )
    ]

    # 3. Get task via client
    retrieved_task = await client.get_task(request=GetTaskRequest(id=task_id))

    # 4. Assert behavior based on `use_copying`
    if use_copying:
        # The un-saved artifact IS NOT leaked to the client
        assert len(retrieved_task.artifacts) == 0
    else:
        # The un-saved artifact IS leaked to the client
        assert len(retrieved_task.artifacts) == 1
        assert retrieved_task.artifacts[0].name == 'leaked-artifact'
