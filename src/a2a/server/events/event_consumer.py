import asyncio
import logging
import sys

from collections.abc import AsyncGenerator

from pydantic import ValidationError

from a2a.server.events.event_queue import Event, EventQueue
from a2a.types import (
    InternalError,
    Message,
    Task,
    TaskState,
    TaskStatusUpdateEvent,
)
from a2a.utils.errors import ServerError
from a2a.utils.telemetry import SpanKind, trace_class


# This is an alias to the exception for closed queue
QueueClosed: type[Exception] = asyncio.QueueEmpty

# When using python 3.13 or higher, the closed queue signal is QueueShutdown
if sys.version_info >= (3, 13):
    QueueClosed = asyncio.QueueShutDown

logger = logging.getLogger(__name__)


@trace_class(kind=SpanKind.SERVER)
class EventConsumer:
    """Consumer to read events from the agent event queue."""

    def __init__(self, queue: EventQueue):
        """Initializes the EventConsumer.

        Args:
            queue: The `EventQueue` instance to consume events from.
        """
        self.queue = queue
        self._timeout = 0.5
        self._exception: BaseException | None = None
        logger.debug('EventConsumer initialized')

    async def consume_one(self) -> Event:
        """Consume one event from the agent event queue non-blocking.

        Returns:
            The next event from the queue.

        Raises:
            ServerError: If the queue is empty when attempting to dequeue
                immediately.
        """
        logger.debug('Attempting to consume one event.')
        try:
            event = await self.queue.dequeue_event(no_wait=True)
        except asyncio.QueueEmpty as e:
            logger.warning('Event queue was empty in consume_one.')
            raise ServerError(
                InternalError(message='Agent did not return any response')
            ) from e

        logger.debug('Dequeued event of type: %s in consume_one.', type(event))

        self.queue.task_done()

        return event

    async def consume_all(self) -> AsyncGenerator[Event]:
        """Consume all the generated streaming events from the agent.

        This method yields events as they become available from the queue
        until a final event is received or the queue is closed. It also
        monitors for exceptions set by the `agent_task_callback`.

        Yields:
            Events dequeued from the queue.

        Raises:
            BaseException: If an exception was set by the `agent_task_callback`.
        """
        logger.debug('Starting to consume all events from the queue.')
        while True:
            if self._exception:
                raise self._exception
            try:
                # We use a timeout when waiting for an event from the queue.
                # This is required because it allows the loop to check if
                # `self._exception` has been set by the `agent_task_callback`.
                # Without the timeout, loop might hang indefinitely if no events are
                # enqueued by the agent and the agent simply threw an exception
                event = await asyncio.wait_for(
                    self.queue.dequeue_event(), timeout=self._timeout
                )
                logger.debug(
                    'Dequeued event of type: %s in consume_all.', type(event)
                )
                self.queue.task_done()
                logger.debug(
                    'Marked task as done in event queue in consume_all'
                )

                is_final_event = (
                    (isinstance(event, TaskStatusUpdateEvent) and event.final)
                    or isinstance(event, Message)
                    or (
                        isinstance(event, Task)
                        and event.status.state
                        in (
                            TaskState.completed,
                            TaskState.canceled,
                            TaskState.failed,
                            TaskState.rejected,
                            TaskState.unknown,
                            TaskState.input_required,
                        )
                    )
                )

                # Make sure the yield is after the close events, otherwise
                # the caller may end up in a blocked state where this
                # generator isn't called again to close things out and the
                # other part is waiting for an event or a closed queue.
                if is_final_event:
                    logger.debug('Stopping event consumption in consume_all.')
                    await self.queue.close(True)
                    yield event
                    break
                yield event
            except TimeoutError:
                # continue polling until there is a final event
                continue
            except asyncio.TimeoutError:  # pyright: ignore [reportUnusedExcept]
                # This class was made an alias of built-in TimeoutError after 3.11
                continue
            except (QueueClosed, asyncio.QueueEmpty):
                # Confirm that the queue is closed, e.g. we aren't on
                # python 3.12 and get a queue empty error on an open queue
                if self.queue.is_closed():
                    break
            except ValidationError:
                logger.exception('Invalid event format received')
                continue
            except Exception as e:
                logger.exception('Stopping event consumption due to exception')
                self._exception = e
                continue

    def agent_task_callback(self, agent_task: asyncio.Task[None]) -> None:
        """Callback to handle exceptions from the agent's execution task.

        If the agent's asyncio task raises an exception, this callback is
        invoked, and the exception is stored to be re-raised by the consumer loop.

        Args:
            agent_task: The asyncio.Task that completed.
        """
        logger.debug('Agent task callback triggered.')
        if not agent_task.cancelled() and agent_task.done():
            self._exception = agent_task.exception()
