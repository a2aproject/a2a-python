from typing import Any

from a2a.server.context import ServerCallContext
from a2a.server.id_generator import (
    IDGenerator,
    IDGeneratorContext,
    UUIDGenerator,
)
from a2a.types import (
    InvalidParamsError,
    Message,
    MessageSendConfiguration,
    MessageSendParams,
    Task,
)
from a2a.utils import get_message_text
from a2a.utils.errors import ServerError


class RequestContext:
    """Request Context.

    Holds information about the current request being processed by the server,
    including the incoming message, task and context identifiers, and related
    tasks.
    """

    def __init__(  # noqa: PLR0913
        self,
        request: MessageSendParams | None = None,
        task_id: str | None = None,
        context_id: str | None = None,
        task: Task | None = None,
        related_tasks: list[Task] | None = None,
        call_context: ServerCallContext | None = None,
        task_id_generator: IDGenerator | None = None,
        context_id_generator: IDGenerator | None = None,
    ):
        """Initializes the RequestContext.

        Args:
            request: The incoming `MessageSendParams` request payload.
            task_id: The ID of the task explicitly provided in the request or path.
            context_id: The ID of the context explicitly provided in the request or path.
            task: The existing `Task` object retrieved from the store, if any.
            related_tasks: A list of other tasks related to the current request (e.g., for tool use).
            call_context: The server call context associated with this request.
            task_id_generator: ID generator for new task IDs. Defaults to UUID generator.
            context_id_generator: ID generator for new context IDs. Defaults to UUID generator.
        """
        if related_tasks is None:
            related_tasks = []
        self._params = request
        self._task_id = task_id
        self._context_id = context_id
        self._current_task = task
        self._related_tasks = related_tasks
        self._call_context = call_context
        self._task_id_generator = (
            task_id_generator if task_id_generator else UUIDGenerator()
        )
        self._context_id_generator = (
            context_id_generator if context_id_generator else UUIDGenerator()
        )
        # If the task id and context id were provided, make sure they
        # match the request. Otherwise, create them
        if self._params:
            if task_id:
                self._params.message.task_id = task_id
                if task and task.id != task_id:
                    raise ServerError(InvalidParamsError(message='bad task id'))
            else:
                self._check_or_generate_task_id()
            if context_id:
                self._params.message.context_id = context_id
                if task and task.context_id != context_id:
                    raise ServerError(
                        InvalidParamsError(message='bad context id')
                    )
            else:
                self._check_or_generate_context_id()

    def get_user_input(self, delimiter: str = '\n') -> str:
        """Extracts text content from the user's message parts.

        Args:
            delimiter: The string to use when joining multiple text parts.

        Returns:
            A single string containing all text content from the user message,
            joined by the specified delimiter. Returns an empty string if no
            user message is present or if it contains no text parts.
        """
        if not self._params:
            return ''

        return get_message_text(self._params.message, delimiter)

    def attach_related_task(self, task: Task) -> None:
        """Attaches a related task to the context.

        This is useful for scenarios like tool execution where a new task
        might be spawned.

        Args:
            task: The `Task` object to attach.
        """
        self._related_tasks.append(task)

    @property
    def message(self) -> Message | None:
        """The incoming `Message` object from the request, if available."""
        return self._params.message if self._params else None

    @property
    def related_tasks(self) -> list[Task]:
        """A list of tasks related to the current request."""
        return self._related_tasks

    @property
    def current_task(self) -> Task | None:
        """The current `Task` object being processed."""
        return self._current_task

    @current_task.setter
    def current_task(self, task: Task) -> None:
        """Sets the current task object."""
        self._current_task = task

    @property
    def task_id(self) -> str | None:
        """The ID of the task associated with this context."""
        return self._task_id

    @property
    def context_id(self) -> str | None:
        """The ID of the conversation context associated with this task."""
        return self._context_id

    @property
    def configuration(self) -> MessageSendConfiguration | None:
        """The `MessageSendConfiguration` from the request, if available."""
        return self._params.configuration if self._params else None

    @property
    def call_context(self) -> ServerCallContext | None:
        """The server call context associated with this request."""
        return self._call_context

    @property
    def metadata(self) -> dict[str, Any]:
        """Metadata associated with the request, if available."""
        return self._params.metadata or {} if self._params else {}

    def add_activated_extension(self, uri: str) -> None:
        """Add an extension to the set of activated extensions for this request.

        This causes the extension to be indicated back to the client in the
        response.
        """
        if self._call_context:
            self._call_context.activated_extensions.add(uri)

    @property
    def requested_extensions(self) -> set[str]:
        """Extensions that the client requested to activate."""
        return (
            self._call_context.requested_extensions
            if self._call_context
            else set()
        )

    def _check_or_generate_task_id(self) -> None:
        """Ensures a task ID is present, generating one if necessary."""
        if not self._params:
            return

        if not self._task_id and not self._params.message.task_id:
            self._params.message.task_id = self._task_id_generator.generate(
                IDGeneratorContext(context_id=self._context_id)
            )
        if self._params.message.task_id:
            self._task_id = self._params.message.task_id

    def _check_or_generate_context_id(self) -> None:
        """Ensures a context ID is present, generating one if necessary."""
        if not self._params:
            return

        if not self._context_id and not self._params.message.context_id:
            self._params.message.context_id = (
                self._context_id_generator.generate(
                    IDGeneratorContext(task_id=self._task_id)
                )
            )
        if self._params.message.context_id:
            self._context_id = self._params.message.context_id
