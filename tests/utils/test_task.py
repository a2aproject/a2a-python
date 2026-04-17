import unittest
import uuid

from unittest.mock import patch

import pytest

from a2a.types.a2a_pb2 import (
    Artifact,
    Message,
    Part,
    Role,
    TaskState,
    GetTaskRequest,
    SendMessageConfiguration,
)
from a2a.helpers.proto_helpers import new_task
from a2a.utils.task import (
    apply_history_length,
    decode_page_token,
    encode_page_token,
)
from a2a.utils.errors import InvalidParamsError


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
