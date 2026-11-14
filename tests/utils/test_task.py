import unittest

from base64 import urlsafe_b64encode

import pytest

from a2a.helpers.proto_helpers import new_task
from a2a.types.a2a_pb2 import (
    Artifact,
    GetTaskRequest,
    Message,
    Part,
    Role,
    SendMessageConfiguration,
    TaskState,
)
from a2a.utils.errors import InvalidParamsError
from a2a.utils.task import (
    ListTasksCursor,
    apply_history_length,
    decode_list_tasks_cursor,
    decode_page_token,
    encode_list_tasks_cursor,
    encode_page_token,
)


class TestTask(unittest.TestCase):
    page_token = 'd47a95ba-0f39-4459-965b-3923cdd2ff58'
    encoded_page_token = 'ZDQ3YTk1YmEtMGYzOS00NDU5LTk2NWItMzkyM2NkZDJmZjU4'  # base64 for 'd47a95ba-0f39-4459-965b-3923cdd2ff58'

    def test_encode_page_token(self):
        assert encode_page_token(self.page_token) == self.encoded_page_token

    def test_decode_page_token_succeeds(self):
        assert decode_page_token(self.encoded_page_token) == self.page_token

    def test_decode_page_token_fails(self):
        with pytest.raises(InvalidParamsError) as excinfo:
            decode_page_token('invalid')

        assert 'Token is not a valid base64-encoded cursor.' in str(
            excinfo.value
        )


@pytest.mark.parametrize(
    'cursor',
    [
        ListTasksCursor(timestamp_ns=1_735_689_600_123_000_001, task_id='t-1'),
        ListTasksCursor(timestamp_ns=-1, task_id='before-epoch'),
        ListTasksCursor(timestamp_ns=None, task_id='no-timestamp'),
        ListTasksCursor(timestamp_ns=0, task_id='ünïcode/+='),
        ListTasksCursor(timestamp_ns=1, task_id='x' * 1_000),
    ],
)
def test_list_tasks_cursor_round_trips(cursor: ListTasksCursor) -> None:
    token = encode_list_tasks_cursor(cursor)

    assert decode_list_tasks_cursor(token) == cursor
    # Safe to put in a query string without escaping.
    assert not set(token) & set('+/=')


def test_legacy_task_id_token_is_not_a_cursor() -> None:
    assert decode_list_tasks_cursor(encode_page_token('task-1')) is None
    assert decode_list_tasks_cursor(encode_page_token('{"ts":1}')) is None
    assert decode_list_tasks_cursor('invalid') is None


@pytest.mark.parametrize(
    'payload',
    [
        b'{"ts":1}',
        b'{"id":"t"}',
        b'{"ts":1,"id":"t","extra":0}',
        b'{"ts":1,"id":""}',
        b'{"ts":"1","id":"t"}',
        b'{"ts":true,"id":"t"}',
        b'{"ts":1.5,"id":"t"}',
    ],
)
def test_incomplete_cursor_token_is_not_a_cursor(payload: bytes) -> None:
    """Falls back to the legacy path, which rejects it as an unknown task."""
    token = urlsafe_b64encode(payload).decode().rstrip('=')

    assert decode_list_tasks_cursor(token) is None


def test_cursor_sort_key_orders_missing_timestamps_last() -> None:
    dated = ListTasksCursor(timestamp_ns=0, task_id='a')
    undated = ListTasksCursor(timestamp_ns=None, task_id='z')

    assert undated.sort_key() < dated.sort_key()


class TestApplyHistoryLength(unittest.TestCase):
    def setUp(self):
        self.history = [
            Message(
                message_id=str(i),
                role=Role.ROLE_USER,
                parts=[Part(text=f'msg {i}')],
            )
            for i in range(5)
        ]
        artifacts = [Artifact(artifact_id='a1', parts=[Part(text='a')])]
        self.task = new_task(
            task_id='t1',
            context_id='c1',
            state=TaskState.TASK_STATE_COMPLETED,
            artifacts=artifacts,
            history=self.history,
        )

    def test_none_config_returns_full_history(self):
        result = apply_history_length(self.task, None)
        self.assertEqual(len(result.history), 5)
        self.assertEqual(result.history, self.history)

    def test_unset_history_length_returns_full_history(self):
        result = apply_history_length(self.task, GetTaskRequest())
        self.assertEqual(len(result.history), 5)
        self.assertEqual(result.history, self.history)

    def test_positive_history_length_truncates(self):
        result = apply_history_length(
            self.task, GetTaskRequest(history_length=2)
        )
        self.assertEqual(len(result.history), 2)
        self.assertEqual(result.history, self.history[-2:])

    def test_large_history_length_returns_full_history(self):
        result = apply_history_length(
            self.task, GetTaskRequest(history_length=10)
        )
        self.assertEqual(len(result.history), 5)
        self.assertEqual(result.history, self.history)

    def test_zero_history_length_returns_empty_history(self):
        result = apply_history_length(
            self.task, SendMessageConfiguration(history_length=0)
        )
        self.assertEqual(len(result.history), 0)


if __name__ == '__main__':
    unittest.main()
