import httpx

from fastapi import FastAPI

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.apps import A2ARESTFastAPIApplication
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import (
    BasePushNotificationSender,
    InMemoryPushNotificationConfigStore,
    InMemoryTaskStore,
    TaskUpdater,
)
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentSkill,
    InvalidParamsError,
    Message,
    Task,
)
from a2a.utils import (
    new_agent_text_message,
    new_task,
)
from a2a.utils.errors import ServerError


def test_agent_card(url: str) -> AgentCard:
    """Returns an agent card for the test agent."""
    return AgentCard(
        name='Test Agent',
        description='Just a test agent',
        url=url,
        version='1.0.0',
        default_input_modes=['text'],
        default_output_modes=['text'],
        capabilities=AgentCapabilities(streaming=True, push_notifications=True),
        skills=[
            AgentSkill(
                id='greeting',
                name='Greeting Agent',
                description='just greets the user',
                tags=['greeting'],
                examples=['Hello Agent!', 'How are you?'],
            )
        ],
        supports_authenticated_extended_card=True,
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
            or msg.parts[0].root.kind != 'text'
        ):
            await updater.failed(
                new_agent_text_message(
                    'Unsupported message.', task.context_id, task.id
                )
            )
            return
        text_message = msg.parts[0].root.text

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
            raise ServerError(error=InvalidParamsError(message='No message'))

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
) -> FastAPI:
    """Creates a new HTTP+REST FastAPI application for the test agent."""
    push_config_store = InMemoryPushNotificationConfigStore()
    app = A2ARESTFastAPIApplication(
        agent_card=test_agent_card(url),
        http_handler=DefaultRequestHandler(
            agent_executor=TestAgentExecutor(),
            task_store=InMemoryTaskStore(),
            push_config_store=push_config_store,
            push_sender=BasePushNotificationSender(
                httpx_client=notification_client,
                config_store=push_config_store,
            ),
        ),
    )
    return app.build()
