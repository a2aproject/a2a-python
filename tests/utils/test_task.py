import unittest

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
    MAX_TASK_ID_LENGTH,
    apply_history_length,
    decode_page_token,
    encode_page_token,
    validate_message_content,
    validate_task_id,
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


class TestValidateTaskId(unittest.TestCase):
    def test_valid_task_id_passes(self):
        # Does not raise
        validate_task_id('task-123')
        validate_task_id('a' * MAX_TASK_ID_LENGTH)

    def test_empty_task_id_raises(self):
        with pytest.raises(InvalidParamsError) as excinfo:
            validate_task_id('')
        assert 'non-empty' in str(excinfo.value)

    def test_overlong_task_id_raises(self):
        with pytest.raises(InvalidParamsError) as excinfo:
            validate_task_id('a' * (MAX_TASK_ID_LENGTH + 1))
        assert str(MAX_TASK_ID_LENGTH) in str(excinfo.value)


class TestValidateMessageContent(unittest.TestCase):
    def _message(self, parts: list[Part]) -> Message:
        return Message(message_id='m1', role=Role.ROLE_USER, parts=parts)

    def test_message_with_text_part_passes(self):
        validate_message_content(self._message([Part(text='hello')]))

    def test_message_with_url_part_passes(self):
        validate_message_content(self._message([Part(url='http://x.com/f')]))

    def test_message_with_data_part_passes(self):
        part = Part()
        part.data.string_value = 'x'
        validate_message_content(self._message([part]))

    def test_message_without_parts_raises(self):
        with pytest.raises(InvalidParamsError) as excinfo:
            validate_message_content(self._message([]))
        assert 'at least one part' in str(excinfo.value)

    def test_message_with_empty_part_raises(self):
        with pytest.raises(InvalidParamsError) as excinfo:
            validate_message_content(self._message([Part(media_type='text')]))
        assert 'not be empty' in str(excinfo.value)


if __name__ == '__main__':
    unittest.main()
