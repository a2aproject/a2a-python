from abc import ABC, abstractmethod

from a2a.types.a2a_pb2 import (
    Task,
    TaskArtifactUpdateEvent,
    TaskStatusUpdateEvent,
)


PushNotificationEvent = Task | TaskStatusUpdateEvent | TaskArtifactUpdateEvent


class PushNotificationSender(ABC):
    """Interface for sending push notifications for tasks."""

    @abstractmethod
    async def send_notification(
        self, task_id: str, event: PushNotificationEvent
    ) -> None:
        """Sends a push notification containing the latest task state.

        Implementations are treated as best-effort: the framework catches
        and logs any exception raised here, so a failure never affects the
        task lifecycle or the event stream.
        """
