import httpx

from fastapi import FastAPI

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.context import ServerCallContext
from a2a.server.events import EventQueue
from starlette.applications import Starlette
from a2a.server.routes.rest_routes import create_rest_routes
from a2a.server.routes import create_agent_card_routes
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import (
    BasePushNotificationSender,
    InMemoryPushNotificationConfigStore,
    InMemoryTaskStore,
    TaskUpdater,
)
from a2a.types import InvalidParamsError
from a2a.types.a2a_pb2 import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
    Message,
    Task,
)
from a2a.utils import (
    new_agent_text_message,
    new_task,
)


def test_agent_card(url: str) -> AgentCard:
    """Returns an agent card for the test agent."""
    return AgentCard(
        name='Test Agent',
        description='Just a test agent',
        version='1.0.0',
        default_input_modes=['text'],
        default_output_modes=['text'],
        capabilities=AgentCapabilities(
            streaming=True,
            push_notifications=True,
            extended_agent_card=True,
        ),
        skills=[
            AgentSkill(
                id='greeting',
                name='Greeting Agent',
                description='just greets the user',
                tags=['greeting'],
                examples=['Hello Agent!', 'How are you?'],
            )
        ],
        supported_interfaces=[
            AgentInterface(
                url=url,
                protocol_binding='HTTP+JSON',
            )
        ],
    )


class TestAgent:
    """Agent for push notification testing."""

    async def invoke(
        self, updater: TaskUpdater, msg: Message, task: Task
    ) -> None:
        # Fail for unsupported messages.
        if (
            not msg.parts
            or len(msg.parts) != 1
            or not msg.parts[0].HasField('text')
        ):
            await updater.failed(
                new_agent_text_message(
                    'Unsupported message.', task.context_id, task.id
                )
            )
            return
        text_message = msg.parts[0].text

        # Simple request-response flow.
        if text_message == 'Hello Agent!':
            await updater.complete(
                new_agent_text_message('Hello User!', task.context_id, task.id)
            )

        # Flow with user input required: "How are you?" -> "Good! How are you?" -> "Good" -> "Amazing".
        elif text_message == 'How are you?':
            await updater.requires_input(
                new_agent_text_message(
                    'Good! How are you?', task.context_id, task.id
                )
            )
        elif text_message == 'Good':
            await updater.complete(
                new_agent_text_message('Amazing', task.context_id, task.id)
            )

        # Fail for unsupported messages.
        else:
            await updater.failed(
                new_agent_text_message(
                    'Unsupported message.', task.context_id, task.id
                )
            )


class TestAgentExecutor(AgentExecutor):
    """Test AgentExecutor implementation."""

    def __init__(self) -> None:
        self.agent = TestAgent()

    async def execute(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        if not context.message:
            raise InvalidParamsError(message='No message')

        task = context.current_task
        if not task:
            task = new_task(context.message)
            await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue, task.id, task.context_id)

        await self.agent.invoke(updater, context.message, task)

    async def cancel(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        raise NotImplementedError('cancel not supported')


def create_agent_app(
    url: str, notification_client: httpx.AsyncClient
) -> Starlette:
    """Creates a new HTTP+REST Starlette application for the test agent."""
    push_config_store = InMemoryPushNotificationConfigStore()
    card = test_agent_card(url)
    handler = DefaultRequestHandler(
        agent_executor=TestAgentExecutor(),
        task_store=InMemoryTaskStore(),
        push_config_store=push_config_store,
        push_sender=BasePushNotificationSender(
            httpx_client=notification_client,
            config_store=push_config_store,
            context=ServerCallContext(),
        ),
    )
    rest_routes = create_rest_routes(agent_card=card, request_handler=handler)
    agent_card_routes = create_agent_card_routes(
        agent_card=card, card_url='/.well-known/agent-card.json'
    )
    return Starlette(routes=[*rest_routes, *agent_card_routes])
