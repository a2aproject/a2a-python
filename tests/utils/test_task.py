import unittest
import uuid

from unittest.mock import patch

import pytest

from a2a.types import Artifact, Message, Part, Role, TextPart
from a2a.utils.task import apply_history_length, completed_task, new_task


class TestTask(unittest.TestCase):
    def test_new_task_status(self):
        message = Message(
            role=Role.user,
            parts=[Part(root=TextPart(text='test message'))],
            message_id=str(uuid.uuid4()),
        )
        task = new_task(message)
        self.assertEqual(task.status.state.value, 'submitted')

    @patch('uuid.uuid4')
    def test_new_task_generates_ids(self, mock_uuid4):
        mock_uuid = uuid.UUID('12345678-1234-5678-1234-567812345678')
        mock_uuid4.return_value = mock_uuid
        message = Message(
            role=Role.user,
            parts=[Part(root=TextPart(text='test message'))],
            message_id=str(uuid.uuid4()),
        )
        task = new_task(message)
        self.assertEqual(task.id, str(mock_uuid))
        self.assertEqual(task.context_id, str(mock_uuid))

    def test_new_task_uses_provided_ids(self):
        task_id = str(uuid.uuid4())
        context_id = str(uuid.uuid4())
        message = Message(
            role=Role.user,
            parts=[Part(root=TextPart(text='test message'))],
            message_id=str(uuid.uuid4()),
            task_id=task_id,
            context_id=context_id,
        )
        task = new_task(message)
        self.assertEqual(task.id, task_id)
        self.assertEqual(task.context_id, context_id)

    def test_new_task_initial_message_in_history(self):
        message = Message(
            role=Role.user,
            parts=[Part(root=TextPart(text='test message'))],
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
                parts=[Part(root=TextPart(text='some content'))],
            )
        ]
        task = completed_task(
            task_id=task_id,
            context_id=context_id,
            artifacts=artifacts,
            history=[],
        )
        self.assertEqual(task.status.state.value, 'completed')

    def test_completed_task_assigns_ids_and_artifacts(self):
        task_id = str(uuid.uuid4())
        context_id = str(uuid.uuid4())
        artifacts = [
            Artifact(
                artifact_id='artifact_1',
                parts=[Part(root=TextPart(text='some content'))],
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
        self.assertEqual(task.artifacts, artifacts)

    def test_completed_task_empty_history_if_not_provided(self):
        task_id = str(uuid.uuid4())
        context_id = str(uuid.uuid4())
        artifacts = [
            Artifact(
                artifact_id='artifact_1',
                parts=[Part(root=TextPart(text='some content'))],
            )
        ]
        task = completed_task(
            task_id=task_id, context_id=context_id, artifacts=artifacts
        )
        self.assertEqual(task.history, [])

    def test_completed_task_uses_provided_history(self):
        task_id = str(uuid.uuid4())
        context_id = str(uuid.uuid4())
        artifacts = [
            Artifact(
                artifact_id='artifact_1',
                parts=[Part(root=TextPart(text='some content'))],
            )
        ]
        history = [
            Message(
                role=Role.user,
                parts=[Part(root=TextPart(text='Hello'))],
                message_id=str(uuid.uuid4()),
            ),
            Message(
                role=Role.agent,
                parts=[Part(root=TextPart(text='Hi there'))],
                message_id=str(uuid.uuid4()),
            ),
        ]
        task = completed_task(
            task_id=task_id,
            context_id=context_id,
            artifacts=artifacts,
            history=history,
        )
        self.assertEqual(task.history, history)

    def test_new_task_invalid_message_empty_parts(self):
        with self.assertRaises(ValueError):
            new_task(
                Message(
                    role=Role.user,
                    parts=[],
                    message_id=str(uuid.uuid4()),
                )
            )

    def test_new_task_invalid_message_empty_content(self):
        with self.assertRaises(ValueError):
            new_task(
                Message(
                    role=Role.user,
                    parts=[Part(root=TextPart(text=''))],
                    messageId=str(uuid.uuid4()),
                )
            )

    def test_new_task_invalid_message_none_role(self):
        with self.assertRaises(TypeError):
            msg = Message.model_construct(
                role=None,
                parts=[Part(root=TextPart(text='test message'))],
                message_id=str(uuid.uuid4()),
            )
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
                artifacts=['not an artifact'],
                history=[],
            )

    def test_apply_history_length_cases(self):
        # Setup task with 3 messages
        history = [
            Message(role=Role.user, parts=[Part(root=TextPart(text='1'))], message_id='1'),
            Message(role=Role.agent, parts=[Part(root=TextPart(text='2'))], message_id='2'),
            Message(role=Role.user, parts=[Part(root=TextPart(text='3'))], message_id='3'),
        ]
        task_id = str(uuid.uuid4())
        context_id = str(uuid.uuid4())
        task = completed_task(
            task_id=task_id,
            context_id=context_id,
            artifacts=[Artifact(artifact_id='a', parts=[Part(root=TextPart(text='a'))])],
            history=history
        )

        # historyLength = 0 -> empty
        t0 = apply_history_length(task, 0)
        self.assertEqual(len(t0.history), 0)

        # historyLength = 1 -> last one
        t1 = apply_history_length(task, 1)
        self.assertEqual(len(t1.history), 1)
        self.assertEqual(t1.history[0].message_id, '3')

        # historyLength = 2 -> last two
        t2 = apply_history_length(task, 2)
        self.assertEqual(len(t2.history), 2)
        self.assertEqual(t2.history[0].message_id, '2')
        self.assertEqual(t2.history[1].message_id, '3')

        # historyLength = None -> all
        tn = apply_history_length(task, None)
        self.assertEqual(len(tn.history), 3)

        # historyLength = 10 -> all
        t10 = apply_history_length(task, 10)
        self.assertEqual(len(t10.history), 3)


if __name__ == '__main__':
    unittest.main()
