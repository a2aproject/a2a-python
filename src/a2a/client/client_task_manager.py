import logging

from a2a.client.errors import (
    A2AClientInvalidArgsError,
    A2AClientInvalidStateError,
)
from a2a.types.a2a_pb2 import (
    Message,
    StreamResponse,
    Task,
    TaskState,
    TaskStatus,
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

    async def process(
        self,
        event: StreamResponse,
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
        if event.HasField('msg'):
            # Messages are not processed here.
            return None

        if event.HasField('task'):
            if self._current_task:
                raise A2AClientInvalidArgsError(
                    'Task is already set, create new manager for new tasks.'
                )
            await self._save_task(event.task)
            return event.task

        task = self._current_task

        if event.HasField('status_update'):
            status_update = event.status_update
            if not task:
                task = Task(
                    status=TaskStatus(state=TaskState.TASK_STATE_UNSPECIFIED),
                    id=status_update.task_id,
                    context_id=status_update.context_id,
                )

            logger.debug(
                'Updating task %s status to: %s',
                status_update.task_id,
                status_update.status.state,
            )
            if status_update.status.HasField('message'):
                # "Repeated" fields are merged by appending.
                task.history.append(status_update.status.message)

            if status_update.metadata:
                task.metadata.MergeFrom(status_update.metadata)

            task.status.CopyFrom(status_update.status)
            await self._save_task(task)

        if event.HasField('artifact_update'):
            artifact_update = event.artifact_update
            if not task:
                task = Task(
                    status=TaskStatus(state=TaskState.TASK_STATE_UNSPECIFIED),
                    id=artifact_update.task_id,
                    context_id=artifact_update.context_id,
                )

            logger.debug('Appending artifact to task %s', task.id)
            append_artifact_to_task(task, artifact_update)
            await self._save_task(task)

        return self._current_task

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
        if task.status.HasField('message'):
            task.history.append(task.status.message)
            task.status.ClearField('message')

        task.history.append(message)
        self._current_task = task
        return task
