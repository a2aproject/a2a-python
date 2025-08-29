import logging

from a2a.server.context import ServerCallContext
from a2a.server.events.event_queue import Event
from a2a.server.tasks.task_store import TaskStore
from a2a.types import (
    InvalidParamsError,
    Message,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from a2a.utils import append_artifact_to_task
from a2a.utils.errors import ServerError


logger = logging.getLogger(__name__)


class TaskManager:
    """Helps manage a task's lifecycle during execution of a request.

    Responsible for retrieving, saving, and updating the `Task` object based on
    events received from the agent.
    """

    def __init__(
        self,
        task_id: str | None,
        context_id: str | None,
        task_store: TaskStore,
        initial_message: Message | None,
        context: ServerCallContext | None = None,
    ):
        """Initializes the TaskManager.

        Args:
            task_id: The ID of the task, if known from the request.
            context_id: The ID of the context, if known from the request.
            task_store: The `TaskStore` instance for persistence.
            initial_message: The `Message` that initiated the task, if any.
                             Used when creating a new task object.
            context: The `ServerCallContext` that this task is produced under.
        """
        if task_id is not None and not (isinstance(task_id, str) and task_id):
            raise ValueError('Task ID must be a non-empty string')

        self.task_id = task_id
        self.context_id = context_id
        self.task_store = task_store
        self._initial_message = initial_message
        self._current_task: Task | None = None
        self._call_context: ServerCallContext | None = context
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
            ServerError: If the task ID in the event conflicts with the TaskManager's ID
                         when the TaskManager's ID is already set.
        """
        task_id_from_event = (
            event.id if isinstance(event, Task) else event.task_id
        )
        # If task id is known, make sure it is matched
        if self.task_id and self.task_id != task_id_from_event:
            raise ServerError(
                error=InvalidParamsError(
                    message=f"Task in event doesn't match TaskManager {self.task_id} : {task_id_from_event}"
                )
            )
        if not self.task_id:
            self.task_id = task_id_from_event
        if self.context_id and self.context_id != event.context_id:
            raise ServerError(
                error=InvalidParamsError(
                    message=f"Context in event doesn't match TaskManager {self.context_id} : {event.context_id}"
                )
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
            if task.status.message:
                if not task.history:
                    task.history = [task.status.message]
                else:
                    task.history.append(task.status.message)
            if event.metadata:
                if not task.metadata:
                    task.metadata = {}
                task.metadata.update(event.metadata)
            task.status = event.status
        else:
            logger.debug('Appending artifact to task %s', task.id)
            append_artifact_to_task(task, event)

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
        task: Task | None = self._current_task
        if not task and self.task_id:
            logger.debug(
                'Attempting to retrieve existing task with id: %s', self.task_id
            )
            task = await self.task_store.get(self.task_id, self._call_context)

        if not task:
            logger.info(
                'Task not found or task_id not set. Creating new task for event (task_id: %s, context_id: %s).',
                event.task_id,
                event.context_id,
            )
            # streaming agent did not previously stream task object.
            # Create a task object with the available information and persist the event
            task = self._init_task_obj(event.task_id, event.context_id)
            await self._save_task(task)

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
            status=TaskStatus(state=TaskState.submitted),
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
