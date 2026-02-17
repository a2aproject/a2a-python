import unittest
import uuid

from unittest.mock import patch

import pytest

from a2a.types.a2a_pb2 import Artifact, Message, Part, Role, TaskState
from a2a.utils.task import (
    completed_task,
    decode_page_token,
    encode_page_token,
    new_task,
)


class TestTask(unittest.TestCase):
    def test_new_task_status(self):
        message = Message(
            role=Role.ROLE_USER,
            parts=[Part(text='test message')],
            message_id=str(uuid.uuid4()),
        )
        task = new_task(message)
        self.assertEqual(task.status.state, TaskState.TASK_STATE_SUBMITTED)

    @patch('uuid.uuid4')
    def test_new_task_generates_ids(self, mock_uuid4):
        mock_uuid = uuid.UUID('12345678-1234-5678-1234-567812345678')
        mock_uuid4.return_value = mock_uuid
        message = Message(
            role=Role.ROLE_USER,
            parts=[Part(text='test message')],
            message_id=str(uuid.uuid4()),
        )
        task = new_task(message)
        self.assertEqual(task.id, str(mock_uuid))
        self.assertEqual(task.context_id, str(mock_uuid))

    def test_new_task_uses_provided_ids(self):
        task_id = str(uuid.uuid4())
        context_id = str(uuid.uuid4())
        message = Message(
            role=Role.ROLE_USER,
            parts=[Part(text='test message')],
            message_id=str(uuid.uuid4()),
            task_id=task_id,
            context_id=context_id,
        )
        task = new_task(message)
        self.assertEqual(task.id, task_id)
        self.assertEqual(task.context_id, context_id)

    def test_new_task_initial_message_in_history(self):
        message = Message(
            role=Role.ROLE_USER,
            parts=[Part(text='test message')],
            message_id=str(uuid.uuid4()),
        )
        task = new_task(message)
        self.assertEqual(len(task.history), 1)
        self.assertEqual(task.history[0], message)

    def test_completed_task_status(self):
        task_id = str(uuid.uuid4())
        context_id = str(uuid.uuid4())
        artifacts = [
            Artifact(
                artifact_id='artifact_1',
                parts=[Part(text='some content')],
            )
        ]
        task = completed_task(
            task_id=task_id,
            context_id=context_id,
            artifacts=artifacts,
            history=[],
        )
        self.assertEqual(task.status.state, TaskState.TASK_STATE_COMPLETED)

    def test_completed_task_assigns_ids_and_artifacts(self):
        task_id = str(uuid.uuid4())
        context_id = str(uuid.uuid4())
        artifacts = [
            Artifact(
                artifact_id='artifact_1',
                parts=[Part(text='some content')],
            )
        ]
        task = completed_task(
            task_id=task_id,
            context_id=context_id,
            artifacts=artifacts,
            history=[],
        )
        self.assertEqual(task.id, task_id)
        self.assertEqual(task.context_id, context_id)
        self.assertEqual(len(task.artifacts), len(artifacts))

    def test_completed_task_empty_history_if_not_provided(self):
        task_id = str(uuid.uuid4())
        context_id = str(uuid.uuid4())
        artifacts = [
            Artifact(
                artifact_id='artifact_1',
                parts=[Part(text='some content')],
            )
        ]
        task = completed_task(
            task_id=task_id, context_id=context_id, artifacts=artifacts
        )
        self.assertEqual(len(task.history), 0)

    def test_completed_task_uses_provided_history(self):
        task_id = str(uuid.uuid4())
        context_id = str(uuid.uuid4())
        artifacts = [
            Artifact(
                artifact_id='artifact_1',
                parts=[Part(text='some content')],
            )
        ]
        history = [
            Message(
                role=Role.ROLE_USER,
                parts=[Part(text='Hello')],
                message_id=str(uuid.uuid4()),
            ),
            Message(
                role=Role.ROLE_AGENT,
                parts=[Part(text='Hi there')],
                message_id=str(uuid.uuid4()),
            ),
        ]
        task = completed_task(
            task_id=task_id,
            context_id=context_id,
            artifacts=artifacts,
            history=history,
        )
        self.assertEqual(len(task.history), len(history))

    def test_new_task_invalid_message_empty_parts(self):
        with self.assertRaises(ValueError):
            new_task(
                Message(
                    role=Role.ROLE_USER,
                    parts=[],
                    message_id=str(uuid.uuid4()),
                )
            )

    def test_new_task_invalid_message_empty_content(self):
        with self.assertRaises(ValueError):
            new_task(
                Message(
                    role=Role.ROLE_USER,
                    parts=[Part(text='')],
                    message_id=str(uuid.uuid4()),
                )
            )

    def test_new_task_invalid_message_none_role(self):
        # Proto messages always have a default role (ROLE_UNSPECIFIED = 0)
        # Testing with unspecified role
        msg = Message(
            role=Role.ROLE_UNSPECIFIED,
            parts=[Part(text='test message')],
            message_id=str(uuid.uuid4()),
        )
        with self.assertRaises((TypeError, ValueError)):
            new_task(msg)

    def test_completed_task_empty_artifacts(self):
        with pytest.raises(
            ValueError,
            match='artifacts must be a non-empty list of Artifact objects',
        ):
            completed_task(
                task_id='task-123',
                context_id='ctx-456',
                artifacts=[],
                history=[],
            )

    def test_completed_task_invalid_artifact_type(self):
        with pytest.raises(
            ValueError,
            match='artifacts must be a non-empty list of Artifact objects',
        ):
            completed_task(
                task_id='task-123',
                context_id='ctx-456',
                artifacts=['not an artifact'],  # type: ignore[arg-type]
                history=[],
            )

    page_token = 'd47a95ba-0f39-4459-965b-3923cdd2ff58'
    encoded_page_token = 'ZDQ3YTk1YmEtMGYzOS00NDU5LTk2NWItMzkyM2NkZDJmZjU4'  # base64 for 'd47a95ba-0f39-4459-965b-3923cdd2ff58'

    def test_encode_page_token(self):
        assert encode_page_token(self.page_token) == self.encoded_page_token

    def test_decode_page_token_succeeds(self):
        assert decode_page_token(self.encoded_page_token) == self.page_token

    def test_decode_page_token_fails(self):
        with pytest.raises(ValueError) as excinfo:
            decode_page_token('invalid')

        assert 'Token is not a valid base64-encoded cursor.' in str(
            excinfo.value
        )


if __name__ == '__main__':
    unittest.main()
