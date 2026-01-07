import asyncio
import uuid
import pytest

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.context import ServerCallContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers.default_request_handler import (
    DefaultRequestHandler,
)
from a2a.server.tasks import TaskStore
from a2a.server.tasks.task_updater import TaskUpdater
from a2a.types import (
    Message,
    MessageSendParams,
    Part,
    Role,
    Task,
    TaskState,
    TextPart,
)


class FailingTaskStore(TaskStore):
    """Task store that fails on save to simulate a poisoned configuration."""

    async def get(
        self, task_id: str, context: ServerCallContext | None = None
    ) -> Task | None:
        """Return None for simplicity."""
        return None

    async def save(
        self, task: Task, context: ServerCallContext | None = None
    ) -> None:
        """Always fail to simulate task store error."""
        raise RuntimeError(
            'This is an Error!'
        )

    async def delete(
        self, task_id: str, context: ServerCallContext | None = None
    ) -> None:
        """No-op for simplicity."""


class HelloWorldAgent:
    """Hello World Agent."""

    async def invoke(self) -> str:
        return 'Hello World'

class HelloWorldAgentExecutor(AgentExecutor):
    """Test Agent Implementation."""

    def __init__(self):
        self.agent = HelloWorldAgent()

    async def execute(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        updater = TaskUpdater(
            event_queue,
            task_id=context.task_id or str(uuid.uuid4()),
            context_id=context.context_id or str(uuid.uuid4()),
        )
        # raise ValueError("Simulated error during task execution")
        if not context.task_id:
            await updater.submit()
        await updater.update_status(TaskState.working)
        result = await self.agent.invoke()
        await updater.add_artifact([Part(root=TextPart(text=result))])
        await updater.complete()

    async def cancel(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        raise NotImplementedError('cancel not supported')

@pytest.mark.asyncio
async def test_hanging_on_task_save_error() -> None:
    """Test that demonstrates hanging when task save fails.
    """
    agent = HelloWorldAgentExecutor()
    task_store = FailingTaskStore()
    handler = DefaultRequestHandler(
        agent_executor=agent, task_store=task_store
    )

    params = MessageSendParams(
        message=Message(
            role=Role.user,
            parts=[TextPart(text='Test message')],
            message_id=str(uuid.uuid4()),
        )
    )

    try:
        # Use a short timeout to fail fast
        await asyncio.wait_for(
            handler.on_message_send(params), timeout=2.0
        )
    except RuntimeError as e:
        assert str(e) == 'This is an Error!'
    except asyncio.TimeoutError:
        # If we get here, it means it hung!
        pytest.fail("Test hung and timed out! Fix failed.")