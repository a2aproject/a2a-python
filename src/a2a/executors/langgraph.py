from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import Event, EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import Task, TaskState
from a2a.types import (
    InternalError,
    InvalidParamsError,
    Part,
    TaskState,
    TextPart,
    UnsupportedOperationError,
)
from a2a.utils import (
    new_agent_text_message,
    new_task,
)
from langgraph.graph.state import CompiledStateGraph
import logging


class LanggraphAgentExecutor(AgentExecutor):
    def __init__(self, graph: CompiledStateGraph):
        self.graph = graph

    async def execute(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        query = context.get_user_input()
        task = context.current_task
        if not task:
            task = new_task(context.message)
            await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue, task.id, task.context_id)
        try:
            inputs = {'messages': [{'role': 'user', 'content': query}]}
            config = {'configurable': {'thread_id': task.context_id}}

            for item in self.graph.stream(inputs, config, stream_mode='values'):
                if item.get('next', None) == 'FINISH':
                    await updater.complete(
                        message=new_agent_text_message(
                            item['messages'][-1].content,
                            task.contextId,
                            task.id,
                        )
                    )
                else:
                    await updater.update_status(
                        TaskState.working,
                        new_agent_text_message(
                            item['messages'][-1].content,
                            task.context_id,
                            task.id,
                        ),
                    )
        except Exception:
            logging.exception('An error occurred while streaming the response')

    async def cancel(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        raise ServerError(error=UnsupportedOperationError())

