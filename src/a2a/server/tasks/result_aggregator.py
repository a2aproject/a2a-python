import asyncio
import logging

from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable

from a2a.server.events import Event, EventConsumer
from a2a.server.tasks.task_manager import TaskManager
from a2a.types import Message, Task, TaskState, TaskStatusUpdateEvent


logger = logging.getLogger(__name__)


class ResultAggregator:
    """ResultAggregator is used to process the event streams from an AgentExecutor.

    There are three main ways to use the ResultAggregator:
    1) As part of a processing pipe. consume_and_emit will construct the updated
       task as the events arrive, and re-emit those events for another consumer
    2) As part of a blocking call. consume_all will process the entire stream and
       return the final Task or Message object
    3) As part of a push solution where the latest Task is emitted after processing an event.
       consume_and_emit_task will consume the Event stream, process the events to the current
       Task object and emit that Task object.
    """

    def __init__(
        self,
        task_manager: TaskManager,
    ) -> None:
        """Initializes the ResultAggregator.

        Args:
            task_manager: The `TaskManager` instance to use for processing events
                          and managing the task state.
        """
        self.task_manager = task_manager
        self._message: Message | None = None

    @property
    async def current_result(self) -> Task | Message | None:
        """Returns the current aggregated result (Task or Message).

        This is the latest state processed from the event stream.

        Returns:
            The current `Task` object managed by the `TaskManager`, or the final
            `Message` if one was received, or `None` if no result has been produced yet.
        """
        if self._message:
            return self._message
        return await self.task_manager.get_task()

    async def consume_and_emit(
        self, consumer: EventConsumer
    ) -> AsyncGenerator[Event]:
        """Processes the event stream from the consumer, updates the task state, and re-emits the same events.

        Useful for streaming scenarios where the server needs to observe and
        process events (e.g., save task state, send push notifications) while
        forwarding them to the client.

        Args:
            consumer: The `EventConsumer` to read events from.

        Yields:
            The `Event` objects consumed from the `EventConsumer`.
        """
        async for event in consumer.consume_all():
            await self.task_manager.process(event)
            yield event

    async def consume_all(
        self, consumer: EventConsumer
    ) -> Task | Message | None:
        """Processes the entire event stream from the consumer and returns the final result.

        Blocks until the event stream ends (queue is closed after final event or exception).

        Args:
            consumer: The `EventConsumer` to read events from.

        Returns:
            The final `Task` object or `Message` object after the stream is exhausted.
            Returns `None` if the stream ends without producing a final result.

        Raises:
            BaseException: If the `EventConsumer` raises an exception during consumption.
        """
        async for event in consumer.consume_all():
            if isinstance(event, Message):
                self._message = event
                return event
            await self.task_manager.process(event)
        return await self.task_manager.get_task()

    async def consume_and_break_on_interrupt(
        self,
        consumer: EventConsumer,
        blocking: bool = True,
        event_callback: Callable[[], Awaitable[None]] | None = None,
    ) -> tuple[Task | Message | None, bool]:
        """Processes the event stream until completion or an interruptable state is encountered.

        If `blocking` is False, it returns after the first event that creates a Task or Message.
        If `blocking` is True, it waits for completion unless an `auth_required`
        state is encountered, which is always an interruption.
        If interrupted, consumption continues in a background task.

        Args:
            consumer: The `EventConsumer` to read events from.
            blocking: If `False`, the method returns as soon as a task/message
                      is available. If `True`, it waits for a terminal state.
            event_callback: Optional async callback function to be called after each event
                           is processed in the background continuation.
                           Mainly used for push notifications currently.

        Returns:
            A tuple containing:
            - The current aggregated result (`Task` or `Message`) at the point of completion or interruption.
            - A boolean indicating whether the consumption was interrupted (`True`) or completed naturally (`False`).

        Raises:
            BaseException: If the `EventConsumer` raises an exception during consumption.
        """
        event_stream = consumer.consume_all()
        interrupted = False
        async for event in event_stream:
            if isinstance(event, Message):
                self._message = event
                return event, False
            await self.task_manager.process(event)

            should_interrupt = False
            is_auth_required = (
                isinstance(event, Task | TaskStatusUpdateEvent)
                and event.status.state == TaskState.auth_required
            )

            # Always interrupt on auth_required, as it needs external action.
            if is_auth_required:
                # auth-required is a special state: the message should be
                # escalated back to the caller, but the agent is expected to
                # continue producing events once the authorization is received
                # out-of-band. This is in contrast to input-required, where a
                # new request is expected in order for the agent to make progress,
                # so the agent should exit.
                logger.debug(
                    'Encountered an auth-required task: breaking synchronous message/send flow.'
                )
                should_interrupt = True
            # For non-blocking calls, interrupt as soon as a task is available.
            elif not blocking:
                logger.debug(
                    'Non-blocking call: returning task after first event.'
                )
                should_interrupt = True

            if should_interrupt:
                # Continue consuming the rest of the events in the background.
                # TODO: We should track all outstanding tasks to ensure they eventually complete.
                asyncio.create_task(  # noqa: RUF006
                    self._continue_consuming(event_stream, event_callback)
                )
                interrupted = True
                break
        return await self.task_manager.get_task(), interrupted

    async def _continue_consuming(
        self,
        event_stream: AsyncIterator[Event],
        event_callback: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        """Continues processing an event stream in a background task.

        Used after an interruptable state (like auth_required) is encountered
        in the synchronous consumption flow.

        Args:
            event_stream: The remaining `AsyncIterator` of events from the consumer.
            event_callback: Optional async callback function to be called after each event is processed.
        """
        async for event in event_stream:
            await self.task_manager.process(event)
            if event_callback:
                await event_callback()
