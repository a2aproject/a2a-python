"""Event handling components for the A2A server."""

from a2a.server.events.event_consumer import EventConsumer
from a2a.server.events.event_queue import Event, EventQueue, EventQueueLegacy
from a2a.server.events.in_memory_queue_manager import InMemoryQueueManager
from a2a.server.events.queue_manager import (
    NoTaskQueue,
    QueueManager,
    TaskQueueExists,
)


__all__ = [
    'Event',
    'EventConsumer',
    'EventQueue',
    'EventQueueLegacy',
    'InMemoryQueueManager',
    'NoTaskQueue',
    'QueueManager',
    'TaskQueueExists',
]
