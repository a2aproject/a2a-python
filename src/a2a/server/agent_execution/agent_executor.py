from abc import ABC, abstractmethod

from a2a.server.agent_execution.context import RequestContext
from a2a.server.events.event_queue_v2 import EventQueue


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

        TODO: Document request lifecycle and AgentExecutor responsibilities:
        - Should not close the event_queue.
        - Guarantee single execution per request (no concurrent execution).
        - Throwing exception will result in TaskState.TASK_STATE_ERROR (CHECK!)
        - Once call is completed it should not access context or event_queue
        - Before completing the call it SHOULD update task status to terminal or interrupted state.
        - Explain AUTH_REQUIRED workflow.
        - Explain INPUT_REQUIRED workflow.
        - Explain how cancelation work (executor task will be canceled, cancel() is called, order of calls, etc)
        - Explain if execute can wait for cancel and if cancel can wait for execute.
        - Explain behaviour of streaming / not-immediate when execute() returns in active state.
        - Possible workflows:
            - Enqueue a SINGLE Message object
            - Enqueue TaskStatusUpdateEvent (TASK_STATE_SUBMITTED or TASK_STATE_REJECTED) and continue with TaskStatusUpdateEvent / TaskArtifactUpdateEvent.

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
        `TaskState.TASK_STATE_CANCELED` to the `event_queue`.

        TODO: Document cancelation workflow.
        - What if TaskState.TASK_STATE_CANCELED is not set by cancel() ?
        - How it can interact with execute() ?

        Args:
            context: The request context containing the task ID to cancel.
            event_queue: The queue to publish the cancellation status update to.
        """
