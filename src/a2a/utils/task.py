"""Utility functions for creating A2A Task objects."""

import binascii
import json

from base64 import b64decode, b64encode, urlsafe_b64decode, urlsafe_b64encode
from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

from a2a.types.a2a_pb2 import Task
from a2a.utils.constants import MAX_LIST_TASKS_PAGE_SIZE
from a2a.utils.errors import InvalidParamsError


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


@dataclass(frozen=True)
class ListTasksCursor:
    """A position in the `ListTasks` sort order.

    Tasks are listed by `(has timestamp, timestamp, id)` in descending order,
    so tasks without a timestamp come last. A cursor names the last task of a
    page; the next page starts strictly after it. Because the position is
    carried in the token rather than looked up again, a page token stays valid
    when that task is later updated or deleted.
    """

    timestamp_ns: int | None
    task_id: str

    def sort_key(self) -> tuple[bool, int, str]:
        """The cursor's position as a `(has timestamp, timestamp, id)` key."""
        return (
            self.timestamp_ns is not None,
            self.timestamp_ns or 0,
            self.task_id,
        )


def encode_list_tasks_cursor(cursor: ListTasksCursor) -> str:
    """Encodes a `ListTasksCursor` as an opaque, URL-safe page token."""
    payload = json.dumps(
        {'ts': cursor.timestamp_ns, 'id': cursor.task_id},
        separators=(',', ':'),
    )
    return (
        urlsafe_b64encode(payload.encode(_ENCODING))
        .decode(_ENCODING)
        .rstrip('=')
    )


def decode_list_tasks_cursor(page_token: str) -> ListTasksCursor | None:
    """Decodes a page token produced by `encode_list_tasks_cursor`.

    Args:
        page_token: The page token from a previous `ListTasks` response.

    Returns:
        The decoded cursor, or None if the token is not a valid cursor token.
        Callers treat None as a legacy task-ID token (see
        `decode_page_token`), which also rejects tampered or unknown tokens.
    """
    padded = page_token + '=' * (-len(page_token) % 4)
    try:
        data = json.loads(urlsafe_b64decode(padded.encode(_ENCODING)))
    except (binascii.Error, ValueError):
        return None
    if not isinstance(data, dict) or data.keys() != {'ts', 'id'}:
        return None
    timestamp_ns = data['ts']
    task_id = data['id']
    timestamp_is_valid = timestamp_ns is None or (
        isinstance(timestamp_ns, int) and not isinstance(timestamp_ns, bool)
    )
    if not timestamp_is_valid or not isinstance(task_id, str) or not task_id:
        return None
    return ListTasksCursor(timestamp_ns=timestamp_ns, task_id=task_id)
