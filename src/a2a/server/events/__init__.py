"""Event handling components for the A2A server."""

import logging

from a2a.server.events.event_consumer import EventConsumer
from a2a.server.events.event_queue import Event, EventQueue
from a2a.server.events.in_memory_queue_manager import InMemoryQueueManager
from a2a.server.events.queue_manager import (
    NoTaskQueue,
    QueueManager,
    TaskQueueExists,
)


logger = logging.getLogger(__name__)

try:
    from a2a.server.events.distributed_event_queue import (
        DistributedEventQueue,  # type: ignore
    )
    from a2a.server.events.queue_lifecycle_manager import (
        QueueLifecycleManager,  # type: ignore
        QueueProvisionResult,  # type: ignore
    )
    from a2a.server.events.sns_queue_manager import (
        SnsQueueManager,  # type: ignore
    )
except ImportError as e:
    _original_aws_error = e
    logger.debug(
        'AWS distributed event components not loaded. '
        'Install the aws extra to enable them. Error: %s',
        e,
    )

    class DistributedEventQueue:  # type: ignore
        """Placeholder when aws extra is not installed."""

        def __init__(self, *args, **kwargs):
            raise ImportError(
                'To use DistributedEventQueue, install the aws extra: '
                '\'pip install "a2a-sdk[aws]"\''
            ) from _original_aws_error

    class SnsQueueManager:  # type: ignore
        """Placeholder when aws extra is not installed."""

        def __init__(self, *args, **kwargs):
            raise ImportError(
                'To use SnsQueueManager, install the aws extra: '
                '\'pip install "a2a-sdk[aws]"\''
            ) from _original_aws_error

    class QueueLifecycleManager:  # type: ignore
        """Placeholder when aws extra is not installed."""

        def __init__(self, *args, **kwargs):
            raise ImportError(
                'To use QueueLifecycleManager, install the aws extra: '
                '\'pip install "a2a-sdk[aws]"\''
            ) from _original_aws_error

    class QueueProvisionResult:  # type: ignore
        """Placeholder when aws extra is not installed."""

        def __init__(self, *args, **kwargs):
            raise ImportError(
                'To use QueueProvisionResult, install the aws extra: '
                '\'pip install "a2a-sdk[aws]"\''
            ) from _original_aws_error


__all__ = [
    'DistributedEventQueue',
    'Event',
    'EventConsumer',
    'EventQueue',
    'InMemoryQueueManager',
    'NoTaskQueue',
    'QueueLifecycleManager',
    'QueueManager',
    'QueueProvisionResult',
    'SnsQueueManager',
    'TaskQueueExists',
]
