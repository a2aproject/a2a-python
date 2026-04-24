import httpx

from fastapi import FastAPI
from starlette.applications import Starlette
from starlette.requests import Request

from a2a.auth.user import UnauthenticatedUser, User
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.context import ServerCallContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes
from a2a.server.routes.common import DefaultServerCallContextBuilder
from a2a.server.routes.rest_routes import create_rest_routes
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
from a2a.helpers.proto_helpers import (
    new_text_message,
    new_task_from_user_message,
)


_TEST_USER_HEADER = 'x-test-user'


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
                new_text_message(
                    'Unsupported message.', task.context_id, task.id
                )
            )
            return
        text_message = msg.parts[0].text

        # Simple request-response flow.
        if text_message == 'Hello Agent!':
            await updater.complete(
                new_text_message('Hello User!', task.context_id, task.id)
            )

        # Flow with user input required: "How are you?" -> "Good! How are you?" -> "Good" -> "Amazing".
        elif text_message == 'How are you?':
            await updater.requires_input(
                new_text_message('Good! How are you?', task.context_id, task.id)
            )
        elif text_message == 'Good':
            await updater.complete(
                new_text_message('Amazing', task.context_id, task.id)
            )

        # Fail for unsupported messages.
        else:
            await updater.failed(
                new_text_message(
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
            task = new_task_from_user_message(context.message)
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
    extended_card = test_agent_card(url)
    extended_card.name = 'Test Agent Extended'
    handler = DefaultRequestHandler(
        agent_executor=TestAgentExecutor(),
        task_store=InMemoryTaskStore(),
        agent_card=card,
        extended_agent_card=extended_card,
        push_config_store=push_config_store,
        push_sender=BasePushNotificationSender(
            httpx_client=notification_client,
            config_store=push_config_store,
        ),
    )
    rest_routes = create_rest_routes(request_handler=handler)
    agent_card_routes = create_agent_card_routes(
        agent_card=card, card_url='/.well-known/agent-card.json'
    )
    return Starlette(routes=[*rest_routes, *agent_card_routes])


class _NamedTestUser(User):
    """Authenticated test user identified by ``user_name``."""

    def __init__(self, user_name: str) -> None:
        self._user_name = user_name

    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def user_name(self) -> str:
        return self._user_name


class _HeaderUserContextBuilder(DefaultServerCallContextBuilder):
    """Builds a ServerCallContext whose user is read from a request header."""

    def build_user(self, request: Request) -> User:
        user_name = request.headers.get(_TEST_USER_HEADER)
        if user_name:
            return _NamedTestUser(user_name)
        return UnauthenticatedUser()


def create_multi_user_agent_app(
    url: str, notification_client: httpx.AsyncClient
) -> Starlette:
    """Creates a multi-user variant of the test agent app.

    Differences from create_agent_app:

    - Identity is read from the x-test-user header on each request
      via _HeaderUserContextBuilder. Multiple authenticated
      users (e.g. alice, bob) can therefore call the same
      server.
    - The InMemoryTaskStore uses a constant owner resolver, so
      every authenticated user has access to every task.
    - The InMemoryPushNotificationConfigStore keeps the default
      per-user owner resolver, so each registrar's configs live in their
      own owner partition; this exercises cross-owner aggregation in
      get_info_for_dispatch.
    """
    # Shared task visibility: any authenticated user can see any task.
    task_store = InMemoryTaskStore(owner_resolver=lambda _ctx: 'shared')

    # Per-user push-config partitioning (the default).
    push_config_store = InMemoryPushNotificationConfigStore()

    card = test_agent_card(url)
    extended_card = test_agent_card(url)
    extended_card.name = 'Test Agent Extended'

    handler = DefaultRequestHandler(
        agent_executor=TestAgentExecutor(),
        task_store=task_store,
        agent_card=card,
        extended_agent_card=extended_card,
        push_config_store=push_config_store,
        push_sender=BasePushNotificationSender(
            httpx_client=notification_client,
            config_store=push_config_store,
        ),
    )

    rest_routes = create_rest_routes(
        request_handler=handler,
        context_builder=_HeaderUserContextBuilder(),
    )
    agent_card_routes = create_agent_card_routes(
        agent_card=card, card_url='/.well-known/agent-card.json'
    )
    return Starlette(routes=[*rest_routes, *agent_card_routes])
