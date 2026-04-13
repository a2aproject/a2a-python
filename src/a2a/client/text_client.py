import uuid

from types import TracebackType

from typing_extensions import Self

from a2a.client.client import Client, ClientCallContext
from a2a.types import Message, Part, Role, SendMessageRequest, TaskState
from a2a.utils import get_artifact_text, get_message_text


_TERMINAL_STATES: frozenset[TaskState] = frozenset(
    {
        TaskState.TASK_STATE_COMPLETED,
        TaskState.TASK_STATE_FAILED,
        TaskState.TASK_STATE_CANCELED,
        TaskState.TASK_STATE_REJECTED,
    }
)


class TextClient:
    """A facade around Client that simplifies text-based communication.

    Wraps an underlying Client instance and exposes a simplified interface
    for sending plain-text messages and receiving aggregated text responses.
    Maintains session state (context_id, task_id) automatically across calls.
    For full Client API access, use the underlying client directly via
    the `client` property.
    """

    def __init__(self, client: Client):
        self._client = client
        self._context_id: str = str(uuid.uuid4())
        self._task_id: str | None = None

    async def __aenter__(self) -> Self:
        """Enters the async context manager."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Exits the async context manager and closes the client."""
        await self.close()

    @property
    def client(self) -> Client:
        """Returns the underlying Client instance for full API access."""
        return self._client

    def reset_session(self) -> None:
        """Starts a new session by generating a fresh context ID and clearing the task ID."""
        self._context_id = str(uuid.uuid4())
        self._task_id = None

    async def send_text_message(
        self,
        text: str,
        *,
        delimiter: str = ' ',
        context: ClientCallContext | None = None,
    ) -> str:
        """Sends a text message and returns the aggregated text response.

        Session state (context_id, task_id) is managed automatically across
        calls. Use reset_session() to start a new conversation.

        Args:
            text: The plain-text message to send.
            delimiter: String used to join response parts. Defaults to a
                single space. Use '' for token-streamed responses or a
                newline for paragraph-separated chunks.
            context: Optional call-level context.
        """
        request = SendMessageRequest(
            message=Message(
                role=Role.ROLE_USER,
                message_id=str(uuid.uuid4()),
                context_id=self._context_id,
                task_id=self._task_id,
                parts=[Part(text=text)],
            )
        )

        response_parts: list[str] = []

        async for event in self._client.send_message(request, context=context):
            if event.HasField('task'):
                self._task_id = event.task.id
            elif event.HasField('message'):
                response_parts.append(get_message_text(event.message))
            elif event.HasField('status_update'):
                if event.status_update.task_id:
                    self._task_id = event.status_update.task_id
                if event.status_update.status.state in _TERMINAL_STATES:
                    self._task_id = None
                if event.status_update.status.HasField('message'):
                    response_parts.append(
                        get_message_text(event.status_update.status.message)
                    )
            elif event.HasField('artifact_update'):
                response_parts.append(
                    get_artifact_text(event.artifact_update.artifact)
                )

        return delimiter.join(response_parts)

    async def close(self) -> None:
        """Closes the underlying client."""
        await self._client.close()
