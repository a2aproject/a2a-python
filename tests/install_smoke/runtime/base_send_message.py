"""Drive DefaultRequestHandler.on_message_send end-to-end with no transport.

Exercises the request-validation path that regressed in
https://github.com/a2aproject/a2a-python/pull/1019: a real proto
instance flows through `validate_proto_required_fields`, which broke
on protobuf 7. Pure imports cannot catch this class of regression.
"""

from __future__ import annotations

import asyncio

from a2a.helpers.proto_helpers import new_task_from_user_message
from a2a.server.agent_execution import AgentExecutor
from a2a.server.agent_execution.context import RequestContext
from a2a.server.context import ServerCallContext
from a2a.server.events.event_queue import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.server.tasks.task_updater import TaskUpdater
from a2a.types.a2a_pb2 import (
    AgentCapabilities,
    AgentCard,
    Message,
    Part,
    Role,
    SendMessageConfiguration,
    SendMessageRequest,
    Task,
    TaskState,
)

NAME = 'DefaultRequestHandler.on_message_send roundtrip'


class _HelloAgentExecutor(AgentExecutor):
    async def execute(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        task = context.current_task
        if not task:
            assert context.message is not None
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue, task.id, task.context_id)
        await updater.update_status(
            TaskState.TASK_STATE_WORKING,
            message=updater.new_agent_message([Part(text='I am working')]),
        )
        await updater.add_artifact(
            [Part(text='Hello world!')], name='conversion_result'
        )
        await updater.complete()

    async def cancel(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        pass


async def _run() -> None:
    handler = DefaultRequestHandler(
        agent_executor=_HelloAgentExecutor(),
        task_store=InMemoryTaskStore(),
        agent_card=AgentCard(
            name='smoke',
            version='1.0',
            capabilities=AgentCapabilities(
                streaming=True, push_notifications=False
            ),
        ),
    )
    params = SendMessageRequest(
        message=Message(
            role=Role.ROLE_USER,
            message_id='m1',
            parts=[Part(text='hi')],
        ),
        configuration=SendMessageConfiguration(
            accepted_output_modes=['text/plain']
        ),
    )
    result = await handler.on_message_send(params, ServerCallContext())
    if not isinstance(result, Task):
        raise AssertionError(  # noqa: TRY004
            f'expected Task result, got {type(result).__name__}'
        )
    if result.status.state != TaskState.TASK_STATE_COMPLETED:
        raise AssertionError(
            f'expected TASK_STATE_COMPLETED, got '
            f'{TaskState.Name(result.status.state)}'
        )


def check() -> None:
    asyncio.run(_run())
