import asyncio

from a2a.server.agent_execution import RequestContext, RequestContextBuilder
from a2a.server.context import ServerCallContext
from a2a.server.id_generator import IDGenerator
from a2a.server.tasks import TaskStore
from a2a.types import MessageSendParams, Task


class SimpleRequestContextBuilder(RequestContextBuilder):
    """Builds request context and populates referred tasks."""

    def __init__(
        self,
        should_populate_referred_tasks: bool = False,
        task_store: TaskStore | None = None,
        task_id_generator: IDGenerator | None = None,
        context_id_generator: IDGenerator | None = None,
    ) -> None:
        """Initializes the SimpleRequestContextBuilder.

        Args:
            should_populate_referred_tasks: If True, the builder will fetch tasks
                referenced in `params.message.reference_task_ids` and populate the
                `related_tasks` field in the RequestContext. Defaults to False.
            task_store: The TaskStore instance to use for fetching referred tasks.
                Required if `should_populate_referred_tasks` is True.
            task_id_generator: ID generator for new task IDs. Defaults to None.
            context_id_generator: ID generator for new context IDs. Defaults to None.
        """
        self._task_store = task_store
        self._should_populate_referred_tasks = should_populate_referred_tasks
        self._task_id_generator = task_id_generator
        self._context_id_generator = context_id_generator

    async def build(
        self,
        params: MessageSendParams | None = None,
        task_id: str | None = None,
        context_id: str | None = None,
        task: Task | None = None,
        context: ServerCallContext | None = None,
    ) -> RequestContext:
        """Builds the request context for an agent execution.

        This method assembles the RequestContext object. If the builder was
        initialized with `should_populate_referred_tasks=True`, it fetches all tasks
        referenced in `params.message.reference_task_ids` from the `task_store`.

        Args:
            params: The parameters of the incoming message send request.
            task_id: The ID of the task being executed.
            context_id: The ID of the current execution context.
            task: The primary task object associated with the request.
            context: The server call context, containing metadata about the call.

        Returns:
            An instance of RequestContext populated with the provided information
            and potentially a list of related tasks.
        """
        related_tasks: list[Task] | None = None

        if (
            self._task_store
            and self._should_populate_referred_tasks
            and params
            and params.message.reference_task_ids
        ):
            tasks = await asyncio.gather(
                *[
                    self._task_store.get(task_id)
                    for task_id in params.message.reference_task_ids
                ]
            )
            related_tasks = [x for x in tasks if x is not None]

        return RequestContext(
            request=params,
            task_id=task_id,
            context_id=context_id,
            task=task,
            related_tasks=related_tasks,
            call_context=context,
            task_id_generator=self._task_id_generator,
            context_id_generator=self._context_id_generator,
        )
