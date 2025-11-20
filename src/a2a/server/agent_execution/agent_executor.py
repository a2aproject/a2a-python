from abc import ABC, abstractmethod

from a2a.server.agent_execution.context import RequestContext
from a2a.server.events.event_queue import EventQueue


class AgentExecutor(ABC):
    """Agent Executor interface.

    Implementations of this interface contain the core logic of the agent,
    executing tasks based on requests and publishing updates to an event queue.
    """

    @abstractmethod
    async def execute(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        """Execute the agent's logic for a given request context.

        The agent should read necessary information from the `context` and
        publish `Task` or `Message` events, or `TaskStatusUpdateEvent` /
        `TaskArtifactUpdateEvent` to the `event_queue`. This method should
        return once the agent's execution for this request is complete or
        yields control (e.g., enters an input-required state).

        Args:
            context: The request context containing the message, task ID, etc.
            event_queue: The queue to publish events to.
        """

    @abstractmethod
    async def cancel(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        """Request the agent to cancel an ongoing task.

        The agent should attempt to stop the task identified by the task_id
        in the context and publish a `TaskStatusUpdateEvent` with state
        `TaskState.TASK_STATE_CANCELLED` to the `event_queue`.

        Args:
            context: The request context containing the task ID to cancel.
            event_queue: The queue to publish the cancellation status update to.
        """
