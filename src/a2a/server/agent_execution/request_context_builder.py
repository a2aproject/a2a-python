from abc import ABC, abstractmethod

from a2a.server.agent_execution import RequestContext
from a2a.server.context import ServerCallContext
from a2a.types.a2a_pb2 import MessageSendParams, Task


class RequestContextBuilder(ABC):
    """Builds request context to be supplied to agent executor."""

    @abstractmethod
    async def build(
        self,
        params: MessageSendParams | None = None,
        task_id: str | None = None,
        context_id: str | None = None,
        task: Task | None = None,
        context: ServerCallContext | None = None,
    ) -> RequestContext:
        pass
