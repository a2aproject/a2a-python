import logging

from a2a.client.errors import (
    A2AClientInvalidArgsError,
    A2AClientInvalidStateError,
)
from a2a.server.events.event_queue import Event
from a2a.types import (
    Message,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from a2a.utils import append_artifact_to_task


logger = logging.getLogger(__name__)


class ClientTaskManager:
    """Helps manage a task's lifecycle during execution of a request.

    Responsible for retrieving, saving, and updating the `Task` object based on
    events received from the agent.
    """

    def __init__(
        self,
    ) -> None:
        """Initializes the `ClientTaskManager`."""
        self._current_task: Task | None = None
        self._task_id: str | None = None
        self._context_id: str | None = None

    def get_task(self) -> Task | None:
        """Retrieves the current task object, either from memory.

        If `task_id` is set, it returns `_current_task` otherwise None.

        Returns:
            The `Task` object if found, otherwise `None`.
        """
        if not self._task_id:
            logger.debug('task_id is not set, cannot get task.')
            return None

        return self._current_task

    def get_task_or_raise(self) -> Task:
        """Retrieves the current task object.

        Returns:
            The `Task` object.

        Raises:
            A2AClientInvalidStateError: If there is no current known Task.
        """
        if not (task := self.get_task()):
            # Note: The source of this error is either from bad client usage
            # or from the server sending invalid updates. It indicates that this
            # task manager has not consumed any information about a task, yet
            # the caller is attempting to retrieve the current state of the task
            # it expects to be present.
            raise A2AClientInvalidStateError('no current Task')
        return task

    async def save_task_event(
        self, event: Task | TaskStatusUpdateEvent | TaskArtifactUpdateEvent
    ) -> Task | None:
        """Processes a task-related event (Task, Status, Artifact) and saves the updated task state.

        Ensures task and context IDs match or are set from the event.

        Args:
            event: The task-related event (`Task`, `TaskStatusUpdateEvent`, or `TaskArtifactUpdateEvent`).

        Returns:
            The updated `Task` object after processing the event.

        Raises:
            ClientError: If the task ID in the event conflicts with the TaskManager's ID
                         when the TaskManager's ID is already set.
        """
        if isinstance(event, Task):
            if self._current_task:
                raise A2AClientInvalidArgsError(
                    'Task is already set, create new manager for new tasks.'
                )
            await self._save_task(event)
            return event
        task_id_from_event = (
            event.id if isinstance(event, Task) else event.task_id
        )
        if not self._task_id:
            self._task_id = task_id_from_event
        if not self._context_id:
            self._context_id = event.context_id

        logger.debug(
            'Processing save of task event of type %s for task_id: %s',
            type(event).__name__,
            task_id_from_event,
        )

        task = self._current_task
        if not task:
            task = Task(
                status=TaskStatus(state=TaskState.unknown),
                id=task_id_from_event,
                context_id=self._context_id if self._context_id else '',
            )
        if isinstance(event, TaskStatusUpdateEvent):
            logger.debug(
                'Updating task %s status to: %s',
                event.task_id,
                event.status.state,
            )
            if event.status.message:
                if not task.history:
                    task.history = [event.status.message]
                else:
                    task.history.append(event.status.message)
            if event.metadata:
                if not task.metadata:
                    task.metadata = {}
                task.metadata.update(event.metadata)
            task.status = event.status
        else:
            logger.debug('Appending artifact to task %s', task.id)
            append_artifact_to_task(task, event)
        self._current_task = task
        return task

    async def process(self, event: Event) -> Event:
        """Processes an event, updates the task state if applicable, stores it, and returns the event.

        If the event is task-related (`Task`, `TaskStatusUpdateEvent`, `TaskArtifactUpdateEvent`),
        the internal task state is updated and persisted.

        Args:
            event: The event object received from the agent.

        Returns:
            The same event object that was processed.
        """
        if isinstance(
            event, Task | TaskStatusUpdateEvent | TaskArtifactUpdateEvent
        ):
            await self.save_task_event(event)

        return event

    async def _save_task(self, task: Task) -> None:
        """Saves the given task to the `_current_task` and updated `_task_id` and `_context_id`.

        Args:
            task: The `Task` object to save.
        """
        logger.debug('Saving task with id: %s', task.id)
        self._current_task = task
        if not self._task_id:
            logger.info('New task created with id: %s', task.id)
            self._task_id = task.id
            self._context_id = task.context_id

    def update_with_message(self, message: Message, task: Task) -> Task:
        """Updates a task object adding a new message to its history.

        If the task has a message in its current status, that message is moved
        to the history first.

        Args:
            message: The new `Message` to add to the history.
            task: The `Task` object to update.

        Returns:
            The updated `Task` object (updated in-place).
        """
        if task.status.message:
            if task.history:
                task.history.append(task.status.message)
            else:
                task.history = [task.status.message]
            task.status.message = None
        if task.history:
            task.history.append(message)
        else:
            task.history = [message]
        self._current_task = task
        return task
