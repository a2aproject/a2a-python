import asyncio

from datetime import datetime, timezone
from typing import Any

from google.protobuf.timestamp_pb2 import Timestamp

from a2a.server.events import EventQueue
from a2a.server.id_generator import (
    IDGenerator,
    IDGeneratorContext,
    UUIDGenerator,
)
from a2a.types.a2a_pb2 import (
    Artifact,
    Message,
    Part,
    Role,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)


class TaskUpdater:
    """Helper class for agents to publish updates to a task's event queue.

    Simplifies the process of creating and enqueueing standard task events.
    """

    def __init__(
        self,
        event_queue: EventQueue,
        task_id: str,
        context_id: str,
        artifact_id_generator: IDGenerator | None = None,
        message_id_generator: IDGenerator | None = None,
    ):
        """Initializes the TaskUpdater.

        Args:
            event_queue: The `EventQueue` associated with the task.
            task_id: The ID of the task.
            context_id: The context ID of the task.
            artifact_id_generator: ID generator for new artifact IDs. Defaults to UUID generator.
            message_id_generator: ID generator for new message IDs. Defaults to UUID generator.
        """
        self.event_queue = event_queue
        self.task_id = task_id
        self.context_id = context_id
        self._lock = asyncio.Lock()
        self._terminal_state_reached = False
        self._terminal_states = {
            TaskState.TASK_STATE_COMPLETED,
            TaskState.TASK_STATE_CANCELED,
            TaskState.TASK_STATE_FAILED,
            TaskState.TASK_STATE_REJECTED,
        }
        self._artifact_id_generator = (
            artifact_id_generator if artifact_id_generator else UUIDGenerator()
        )
        self._message_id_generator = (
            message_id_generator if message_id_generator else UUIDGenerator()
        )

    async def update_status(
        self,
        state: TaskState,
        message: Message | None = None,
        timestamp: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Updates the status of the task and publishes a `TaskStatusUpdateEvent`.

        Args:
            state: The new state of the task.
            message: An optional message associated with the status update.
            timestamp: Optional ISO 8601 datetime string. Defaults to current time.
            metadata: Optional metadata for extensions.
        """
        async with self._lock:
            if self._terminal_state_reached:
                raise RuntimeError(
                    f'Task {self.task_id} is already in a terminal state.'
                )
            if state in self._terminal_states:
                self._terminal_state_reached = True

            # Create proto timestamp from datetime
            ts = Timestamp()
            if timestamp:
                # If timestamp string provided, parse it
                dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                ts.FromDatetime(dt)
            else:
                ts.FromDatetime(datetime.now(timezone.utc))

            status = TaskStatus(state=state)
            if message:
                status.message.CopyFrom(message)
            status.timestamp.CopyFrom(ts)

            await self.event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    task_id=self.task_id,
                    context_id=self.context_id,
                    metadata=metadata,
                    status=status,
                )
            )

    async def add_artifact(  # noqa: PLR0913
        self,
        parts: list[Part],
        artifact_id: str | None = None,
        name: str | None = None,
        metadata: dict[str, Any] | None = None,
        append: bool | None = None,
        last_chunk: bool | None = None,
        extensions: list[str] | None = None,
    ) -> None:
        """Adds an artifact chunk to the task and publishes a `TaskArtifactUpdateEvent`.

        Args:
            parts: A list of `Part` objects forming the artifact chunk.
            artifact_id: The ID of the artifact. A new UUID is generated if not provided.
            name: Optional name for the artifact.
            metadata: Optional metadata for the artifact.
            append: Optional boolean indicating if this chunk appends to a previous one.
            last_chunk: Optional boolean indicating if this is the last chunk.
            extensions: Optional list of extensions for the artifact.
        """
        if not artifact_id:
            artifact_id = self._artifact_id_generator.generate(
                IDGeneratorContext(
                    task_id=self.task_id, context_id=self.context_id
                )
            )

        await self.event_queue.enqueue_event(
            TaskArtifactUpdateEvent(
                task_id=self.task_id,
                context_id=self.context_id,
                artifact=Artifact(
                    artifact_id=artifact_id,
                    name=name,
                    parts=parts,
                    metadata=metadata,
                    extensions=extensions,
                ),
                append=append,
                last_chunk=last_chunk,
            )
        )

    async def complete(self, message: Message | None = None) -> None:
        """Marks the task as completed and publishes a final status update."""
        await self.update_status(
            TaskState.TASK_STATE_COMPLETED,
            message=message,
        )

    async def failed(self, message: Message | None = None) -> None:
        """Marks the task as failed and publishes a final status update."""
        await self.update_status(
            TaskState.TASK_STATE_FAILED,
            message=message,
        )

    async def reject(self, message: Message | None = None) -> None:
        """Marks the task as rejected and publishes a final status update."""
        await self.update_status(
            TaskState.TASK_STATE_REJECTED,
            message=message,
        )

    async def submit(self, message: Message | None = None) -> None:
        """Marks the task as submitted and publishes a status update."""
        await self.update_status(
            TaskState.TASK_STATE_SUBMITTED,
            message=message,
        )

    async def start_work(self, message: Message | None = None) -> None:
        """Marks the task as working and publishes a status update."""
        await self.update_status(
            TaskState.TASK_STATE_WORKING,
            message=message,
        )

    async def cancel(self, message: Message | None = None) -> None:
        """Marks the task as cancelled and publishes a finalstatus update."""
        await self.update_status(
            TaskState.TASK_STATE_CANCELED,
            message=message,
        )

    async def requires_input(self, message: Message | None = None) -> None:
        """Marks the task as input required and publishes a status update."""
        await self.update_status(
            TaskState.TASK_STATE_INPUT_REQUIRED,
            message=message,
        )

    async def requires_auth(self, message: Message | None = None) -> None:
        """Marks the task as auth required and publishes a status update."""
        await self.update_status(
            TaskState.TASK_STATE_AUTH_REQUIRED, message=message
        )

    def new_agent_message(
        self,
        parts: list[Part],
        metadata: dict[str, Any] | None = None,
    ) -> Message:
        """Creates a new message object sent by the agent for this task/context.

        Note: This method only *creates* the message object. It does not
              automatically enqueue it.

        Args:
            parts: A list of `Part` objects for the message content.
            metadata: Optional metadata for the message.

        Returns:
            A new `Message` object.
        """
        return Message(
            role=Role.ROLE_AGENT,
            task_id=self.task_id,
            context_id=self.context_id,
            message_id=self._message_id_generator.generate(
                IDGeneratorContext(
                    task_id=self.task_id, context_id=self.context_id
                )
            ),
            metadata=metadata,
            parts=parts,
        )
