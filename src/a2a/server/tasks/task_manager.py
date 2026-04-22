import logging

from a2a.server.context import ServerCallContext
from a2a.server.events.event_queue import Event
from a2a.server.tasks.task_store import TaskStore
from a2a.types.a2a_pb2 import (
    Artifact,
    Message,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from a2a.utils.errors import InvalidParamsError
from a2a.utils.telemetry import trace_function


logger = logging.getLogger(__name__)


@trace_function()
def append_artifact_to_task(task: Task, event: TaskArtifactUpdateEvent) -> None:
    """Helper method for updating a Task object with new artifact data from an event.

    Handles creating the artifacts list if it doesn't exist, adding new artifacts,
    and appending parts to existing artifacts based on the `append` flag in the event.

    Args:
        task: The `Task` object to modify.
        event: The `TaskArtifactUpdateEvent` containing the artifact data.
    """
    new_artifact_data: Artifact = event.artifact
    artifact_id: str = new_artifact_data.artifact_id
    append_parts: bool = event.append

    existing_artifact: Artifact | None = None
    existing_artifact_list_index: int | None = None

    # Find existing artifact by its id
    for i, art in enumerate(task.artifacts):
        if art.artifact_id == artifact_id:
            existing_artifact = art
            existing_artifact_list_index = i
            break

    if not append_parts:
        # This represents the first chunk for this artifact index.
        if existing_artifact_list_index is not None:
            # Replace the existing artifact entirely with the new data
            logger.debug(
                'Replacing artifact at id %s for task %s', artifact_id, task.id
            )
            task.artifacts[existing_artifact_list_index].CopyFrom(
                new_artifact_data
            )
        else:
            # Append the new artifact since no artifact with this index exists yet
            logger.debug(
                'Adding new artifact with id %s for task %s',
                artifact_id,
                task.id,
            )
            task.artifacts.append(new_artifact_data)
    elif existing_artifact:
        # Append new parts to the existing artifact's part list
        logger.debug(
            'Appending parts to artifact id %s for task %s',
            artifact_id,
            task.id,
        )
        existing_artifact.parts.extend(new_artifact_data.parts)
        existing_artifact.metadata.update(
            dict(new_artifact_data.metadata.items())
        )
    else:
        # We received a chunk to append, but we don't have an existing artifact.
        # we will ignore this chunk
        logger.warning(
            'Received append=True for nonexistent artifact index %s in task %s. Ignoring chunk.',
            artifact_id,
            task.id,
        )


class TaskManager:
    """Helps manage a task's lifecycle during execution of a request.

    Responsible for retrieving, saving, and updating the `Task` object based on
    events received from the agent.
    """

    def __init__(
        self,
        task_store: TaskStore,
        context: ServerCallContext,
        task_id: str | None,
        context_id: str | None,
        initial_message: Message | None,
    ):
        """Initializes the TaskManager.

        Args:
            task_store: The `TaskStore` instance for persistence.
            context: The `ServerCallContext` that this task is produced under.
            task_id: The ID of the task, if known from the request.
            context_id: The ID of the context, if known from the request.
            initial_message: The `Message` that initiated the task, if any.
                             Used when creating a new task object.
        """
        if task_id is not None and not (isinstance(task_id, str) and task_id):
            raise ValueError('Task ID must be a non-empty string')

        self.task_store = task_store
        self._call_context: ServerCallContext = context
        self.task_id = task_id
        self.context_id = context_id
        self._initial_message = initial_message
        self._current_task: Task | None = None
        logger.debug(
            'TaskManager initialized with task_id: %s, context_id: %s',
            task_id,
            context_id,
        )

    async def get_task(self) -> Task | None:
        """Retrieves the current task object, either from memory or the store.

        If `task_id` is set, it first checks the in-memory `_current_task`,
        then attempts to load it from the `task_store`.

        Returns:
            The `Task` object if found, otherwise `None`.
        """
        if not self.task_id:
            logger.debug('task_id is not set, cannot get task.')
            return None

        if self._current_task:
            return self._current_task

        logger.debug(
            'Attempting to get task from store with id: %s', self.task_id
        )
        self._current_task = await self.task_store.get(
            self.task_id, self._call_context
        )
        if self._current_task:
            logger.debug('Task %s retrieved successfully.', self.task_id)
        else:
            logger.debug('Task %s not found.', self.task_id)
        return self._current_task

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
            InvalidParamsError: If the task ID in the event conflicts with the TaskManager's ID
                         when the TaskManager's ID is already set.
        """
        task_id_from_event = (
            event.id if isinstance(event, Task) else event.task_id
        )
        # If task id is known, make sure it is matched
        if self.task_id and self.task_id != task_id_from_event:
            raise InvalidParamsError(
                message=f"Task in event doesn't match TaskManager {self.task_id} : {task_id_from_event}"
            )
        if not self.task_id:
            self.task_id = task_id_from_event
        if self.context_id and self.context_id != event.context_id:
            raise InvalidParamsError(
                message=f"Context in event doesn't match TaskManager {self.context_id} : {event.context_id}"
            )
        if not self.context_id:
            self.context_id = event.context_id

        logger.debug(
            'Processing save of task event of type %s for task_id: %s',
            type(event).__name__,
            task_id_from_event,
        )
        if isinstance(event, Task):
            await self._save_task(event)
            return event

        task: Task = await self.ensure_task(event)

        if isinstance(event, TaskStatusUpdateEvent):
            logger.debug(
                'Updating task %s status to: %s', task.id, event.status.state
            )
            if task.status.HasField('message'):
                task.history.append(task.status.message)
            if event.metadata:
                task.metadata.MergeFrom(event.metadata)
            task.status.CopyFrom(event.status)
        else:
            logger.debug('Appending artifact to task %s', task.id)
            append_artifact_to_task(task, event)

        await self._save_task(task)
        return task

    async def ensure_task_id(self, task_id: str, context_id: str) -> Task:
        """Ensures a Task object exists in memory, loading from store or creating new if needed.

        Args:
            task_id: The ID for the new task.
            context_id: The context ID for the new task.

        Returns:
            An existing or newly created `Task` object.
        """
        task: Task | None = self._current_task
        if not task and self.task_id:
            logger.debug(
                'Attempting to retrieve existing task with id: %s', self.task_id
            )
            task = await self.task_store.get(self.task_id, self._call_context)

        if not task:
            logger.info(
                'Task not found or task_id not set. Creating new task for event (task_id: %s, context_id: %s).',
                task_id,
                context_id,
            )
            # streaming agent did not previously stream task object.
            # Create a task object with the available information and persist the event
            task = self._init_task_obj(task_id, context_id)
            await self._save_task(task)

        return task

    async def ensure_task(
        self, event: TaskStatusUpdateEvent | TaskArtifactUpdateEvent
    ) -> Task:
        """Ensures a Task object exists in memory, loading from store or creating new if needed.

        Args:
            event: The task-related event triggering the need for a Task object.

        Returns:
            An existing or newly created `Task` object.
        """
        return await self.ensure_task_id(event.task_id, event.context_id)

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

    def _init_task_obj(self, task_id: str, context_id: str) -> Task:
        """Initializes a new task object in memory.

        Args:
            task_id: The ID for the new task.
            context_id: The context ID for the new task.

        Returns:
            A new `Task` object with initial status and potentially the initial message in history.
        """
        logger.debug(
            'Initializing new Task object with task_id: %s, context_id: %s',
            task_id,
            context_id,
        )
        history = [self._initial_message] if self._initial_message else []
        return Task(
            id=task_id,
            context_id=context_id,
            status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
            history=history,
        )

    async def _save_task(self, task: Task) -> None:
        """Saves the given task to the task store and updates the in-memory `_current_task`.

        Args:
            task: The `Task` object to save.
        """
        logger.debug('Saving task with id: %s', task.id)
        await self.task_store.save(task, self._call_context)
        self._current_task = task
        if not self.task_id:
            logger.info('New task created with id: %s', task.id)
            self.task_id = task.id
            self.context_id = task.context_id

    def update_with_message(self, message: Message, task: Task) -> Task:
        """Updates a task object in memory by adding a new message to its history.

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
