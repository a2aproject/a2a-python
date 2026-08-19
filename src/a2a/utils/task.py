"""Utility functions for creating A2A Task objects."""

import binascii

from base64 import b64decode, b64encode
from typing import Literal, Protocol, runtime_checkable

from a2a.types.a2a_pb2 import Message, Part, Task
from a2a.utils.constants import MAX_LIST_TASKS_PAGE_SIZE
from a2a.utils.errors import InvalidParamsError


MAX_TASK_ID_LENGTH = 1000
"""Maximum allowed length of a task ID."""


def validate_task_id(task_id: str) -> None:
    """Validates that a task ID is non-empty and within the length limit.

    Raises:
        InvalidParamsError: If the task ID is empty or longer than
            ``MAX_TASK_ID_LENGTH`` characters.
    """
    if not task_id:
        raise InvalidParamsError(message='task ID must be non-empty')
    if len(task_id) > MAX_TASK_ID_LENGTH:
        raise InvalidParamsError(
            message=f'task ID must be at most {MAX_TASK_ID_LENGTH} characters'
        )


def validate_message_content(message: Message) -> None:
    """Validates that a message carries actual content.

    A message must contain at least one part, and every part must have
    content (text, raw bytes, a URL or data) rather than being empty.

    Raises:
        InvalidParamsError: If the message has no parts or contains an
            empty part.
    """
    if not message.parts:
        raise InvalidParamsError(
            message='message must contain at least one part'
        )
    for part in message.parts:
        if not _part_has_content(part):
            raise InvalidParamsError(message='message parts must not be empty')


def _part_has_content(part: Part) -> bool:
    """Returns True if a part carries actual content.

    A part has content if it has text, raw bytes or a URL, or a
    non-null ``data`` payload (``google.protobuf.Value`` defaults to
    null/empty).
    """
    if part.text or part.raw or part.url:
        return True
    return (
        part.data.WhichOneof('kind') not in (None, 'null_value')
        if part.HasField('data')
        else False
    )


@runtime_checkable
class HistoryLengthConfig(Protocol):
    """Protocol for configuration arguments containing history_length field."""

    history_length: int

    def HasField(self, field_name: Literal['history_length']) -> bool:  # noqa: N802 -- Protobuf generated code
        """Checks if a field is set.

        This method name matches the generated Protobuf code.
        """
        ...


def validate_history_length(config: HistoryLengthConfig | None) -> None:
    """Validates that history_length is non-negative."""
    if config and config.history_length < 0:
        raise InvalidParamsError(message='history length must be non-negative')


def apply_history_length(
    task: Task, config: HistoryLengthConfig | None
) -> Task:
    """Applies history_length parameter on task and returns a new task object.

    Args:
        task: The original task object with complete history
        config: Configuration object containing 'history_length' field and HasField method.

    Returns:
        A new task object with limited history

    See Also:
        https://a2a-protocol.org/latest/specification/#324-history-length-semantics
    """
    if config is None or not config.HasField('history_length'):
        return task

    history_length = config.history_length

    if history_length == 0:
        if not task.history:
            return task
        task_copy = Task()
        task_copy.CopyFrom(task)
        task_copy.ClearField('history')
        return task_copy

    if history_length > 0 and task.history:
        if len(task.history) <= history_length:
            return task

        task_copy = Task()
        task_copy.CopyFrom(task)
        del task_copy.history[:-history_length]
        return task_copy

    return task


def validate_page_size(page_size: int) -> None:
    """Validates that page_size is in range [1, 100].

    See Also:
        https://a2a-protocol.org/latest/specification/#314-list-tasks
    """
    if page_size < 1:
        raise InvalidParamsError(message='minimum page size is 1')
    if page_size > MAX_LIST_TASKS_PAGE_SIZE:
        raise InvalidParamsError(
            message=f'maximum page size is {MAX_LIST_TASKS_PAGE_SIZE}'
        )


_ENCODING = 'utf-8'


def encode_page_token(task_id: str) -> str:
    """Encodes page token for tasks pagination.

    Args:
        task_id: The ID of the task.

    Returns:
        The encoded page token.
    """
    return b64encode(task_id.encode(_ENCODING)).decode(_ENCODING)


def decode_page_token(page_token: str) -> str:
    """Decodes page token for tasks pagination.

    Args:
        page_token: The encoded page token.

    Returns:
        The decoded task ID.
    """
    encoded_str = page_token
    missing_padding = len(encoded_str) % 4
    if missing_padding:
        encoded_str += '=' * (4 - missing_padding)
    try:
        decoded = b64decode(encoded_str.encode(_ENCODING)).decode(_ENCODING)
    except (binascii.Error, UnicodeDecodeError) as e:
        raise InvalidParamsError(
            'Token is not a valid base64-encoded cursor.'
        ) from e
    return decoded
