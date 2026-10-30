from unittest.mock import AsyncMock, MagicMock

import pytest

from a2a import plugins
from a2a.server.agent_execution import AgentExecutor
from a2a.server.agent_execution.plugin_agent_executor import (
    PluginAgentExecutor,
    PluginEventQueue,
)
from a2a.server.events import EventQueue
from a2a.types.a2a_pb2 import Message, Part, Role


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

    class Agent(AgentExecutor):
        async def execute(self, context, event_queue):
            await event_queue.enqueue_event(message('m-1'))
            if context == 'fail':
                raise ValueError('boom')

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
