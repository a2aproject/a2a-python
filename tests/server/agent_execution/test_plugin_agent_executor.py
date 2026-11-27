import asyncio

from unittest.mock import AsyncMock, MagicMock

import pytest

from a2a import plugins
from a2a.helpers.proto_helpers import new_task_from_user_message
from a2a.server.agent_execution import AgentExecutor
from a2a.server.agent_execution.plugin_agent_executor import (
    PluginAgentExecutor,
    PluginEventQueue,
)
from a2a.server.context import ServerCallContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandlerV2
from a2a.server.tasks import InMemoryTaskStore
from a2a.types.a2a_pb2 import (
    AgentCapabilities,
    AgentCard,
    CancelTaskRequest,
    Message,
    Part,
    Role,
    SendMessageConfiguration,
    SendMessageRequest,
)


class InnerQueue(EventQueue):
    def __init__(self) -> None:
        self.events: list = []
        self.extra = 'forwarded'

    async def enqueue_event(self, event) -> None:
        self.events.append(event)


class Collect(plugins.ExecutionPlugin):
    def __init__(self) -> None:
        self.log: list = []

    async def before_execution(self, request_context) -> None:
        self.log.append('before')

    async def on_execution_event(self, request_context, event) -> None:
        self.log.append(('event', event.message_id))

    async def after_execution(self, request_context, error) -> None:
        self.log.append(('after', error))


def message(message_id: str) -> Message:
    return Message(
        role=Role.ROLE_AGENT, message_id=message_id, parts=[Part(text='x')]
    )


@pytest.mark.asyncio
async def test_event_queue_reports_then_enqueues_and_forwards_attributes():
    collect = Collect()
    inner = InnerQueue()
    queue = PluginEventQueue(inner, plugins.sequential(collect), MagicMock())

    await queue.enqueue_event(message('m-1'))

    assert collect.log == [('event', 'm-1')]
    assert [e.message_id for e in inner.events] == ['m-1']
    assert queue.extra == 'forwarded'
    with pytest.raises(AttributeError):
        _ = queue.missing_attribute


@pytest.mark.asyncio
async def test_executor_wraps_execute_and_passes_errors():
    collect = Collect()
    started = asyncio.Event()

    class Agent(AgentExecutor):
        async def execute(self, context, event_queue):
            await event_queue.enqueue_event(message('m-1'))
            if context == 'fail':
                raise ValueError('boom')
            if context == 'cancel':
                started.set()
                await asyncio.Event().wait()

        async def cancel(self, context, event_queue):
            pass

    agent = Agent()
    executor = PluginAgentExecutor(agent, plugins.sequential(collect))
    assert executor.inner is agent

    inner = InnerQueue()
    await executor.execute(MagicMock(), inner)
    assert collect.log == ['before', ('event', 'm-1'), ('after', None)]
    assert len(inner.events) == 1

    collect.log.clear()
    with pytest.raises(ValueError, match='boom'):
        await executor.execute('fail', InnerQueue())
    assert collect.log[-1][0] == 'after'
    assert isinstance(collect.log[-1][1], ValueError)

    collect.log.clear()
    task = asyncio.create_task(executor.execute('cancel', InnerQueue()))
    await asyncio.wait_for(started.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert collect.log[-1][0] == 'after'
    assert isinstance(collect.log[-1][1], asyncio.CancelledError)


@pytest.mark.asyncio
async def test_cancelled_execution_reaches_after_execution():
    started = asyncio.Event()

    class BlockingAgent(AgentExecutor):
        async def execute(self, context, event_queue):
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
            started.set()
            await asyncio.Event().wait()

        async def cancel(self, context, event_queue):
            pass

    collect = Collect()
    handler = DefaultRequestHandlerV2(
        agent_executor=BlockingAgent(),
        task_store=InMemoryTaskStore(),
        agent_card=AgentCard(
            name='plugin_test_agent',
            version='1.0',
            capabilities=AgentCapabilities(
                streaming=True, push_notifications=True
            ),
        ),
        plugins=plugins.sequential(collect),
    )

    request = SendMessageRequest(
        message=Message(
            role=Role.ROLE_USER, message_id='u-1', parts=[Part(text='hello')]
        )
    )
    request.configuration.CopyFrom(
        SendMessageConfiguration(return_immediately=True)
    )
    result = await handler.on_message_send(request, ServerCallContext())
    await asyncio.wait_for(started.wait(), timeout=5)

    await handler.on_cancel_task(
        CancelTaskRequest(id=result.id), ServerCallContext()
    )

    async def wait_after_execution() -> None:
        while not any(hook == 'after' for hook, *_ in collect.log):
            await asyncio.sleep(0.01)

    await asyncio.wait_for(wait_after_execution(), timeout=5)

    (error,) = [entry[1] for entry in collect.log if entry[0] == 'after']
    assert isinstance(error, asyncio.CancelledError)


@pytest.mark.asyncio
async def test_cancel_delegates_without_hooks():
    collect = Collect()
    agent = MagicMock(spec=AgentExecutor)
    agent.cancel = AsyncMock()
    executor = PluginAgentExecutor(agent, plugins.sequential(collect))
    queue = InnerQueue()

    await executor.cancel('ctx', queue)

    agent.cancel.assert_awaited_once_with('ctx', queue)
    assert collect.log == []
