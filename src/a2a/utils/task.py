"""Utility functions for creating A2A Task objects."""

import uuid

from a2a.types import Artifact, Message, Task, TaskState, TaskStatus, TextPart


def new_task(request: Message) -> Task:
    """Creates a new Task object from an initial user message.

    Generates task and context IDs if not provided in the message.

    Args:
        request: The initial `Message` object from the user.

    Returns:
        A new `Task` object initialized with 'submitted' status and the input message in history.

    Raises:
        TypeError: If the message role is None.
        ValueError: If the message parts are empty, if any part has empty content, or if the provided context_id is invalid.
    """
    if not request.role:
        raise TypeError('Message role cannot be None')
    if not request.parts:
        raise ValueError('Message parts cannot be empty')
    for part in request.parts:
        if isinstance(part.root, TextPart) and not part.root.text:
            raise ValueError('TextPart content cannot be empty')

    return Task(
        status=TaskStatus(state=TaskState.submitted),
        id=request.task_id or str(uuid.uuid4()),
        context_id=request.context_id or str(uuid.uuid4()),
        history=[request],
    )


def completed_task(
    task_id: str,
    context_id: str,
    artifacts: list[Artifact],
    history: list[Message] | None = None,
) -> Task:
    """Creates a Task object in the 'completed' state.

    Useful for constructing a final Task representation when the agent
    finishes and produces artifacts.

    Args:
        task_id: The ID of the task.
        context_id: The context ID of the task.
        artifacts: A list of `Artifact` objects produced by the task.
        history: An optional list of `Message` objects representing the task history.

    Returns:
        A `Task` object with status set to 'completed'.
    """
    if not artifacts or not all(isinstance(a, Artifact) for a in artifacts):
        raise ValueError(
            'artifacts must be a non-empty list of Artifact objects'
        )

    if history is None:
        history = []
    return Task(
        status=TaskStatus(state=TaskState.completed),
        id=task_id,
        context_id=context_id,
        artifacts=artifacts,
        history=history,
    )


def apply_history_length(task: Task, history_length: int | None) -> Task:
    """Applies history_length parameter on task and returns a new task object.

    Args:
        task: The original task object with complete history
        history_length: History length configuration value

    Returns:
        A new task object with limited history
    """
    # Apply historyLength parameter if specified
    if history_length is not None and history_length > 0 and task.history:
        # Limit history to the most recent N messages
        limited_history = task.history[-history_length:]
        # Create a new task instance with limited history
        return task.model_copy(update={'history': limited_history})

    return task
