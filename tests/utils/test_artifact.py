import unittest
import uuid

from unittest.mock import patch

from a2a.types import (
    Artifact,
    DataPart,
    Part,
    TaskArtifactUpdateEvent,
    TextPart,
)
from a2a.utils.artifact import (
    ArtifactStreamer,
    get_artifact_text,
    new_artifact,
    new_data_artifact,
    new_text_artifact,
)


class TestArtifact(unittest.TestCase):
    @patch('uuid.uuid4')
    def test_new_artifact_generates_id(self, mock_uuid4):
        mock_uuid = uuid.UUID('abcdef12-1234-5678-1234-567812345678')
        mock_uuid4.return_value = mock_uuid
        artifact = new_artifact(parts=[], name='test_artifact')
        self.assertEqual(artifact.artifact_id, str(mock_uuid))

    def test_new_artifact_assigns_parts_name_description(self):
        parts = [Part(root=TextPart(text='Sample text'))]
        name = 'My Artifact'
        description = 'This is a test artifact.'
        artifact = new_artifact(parts=parts, name=name, description=description)
        self.assertEqual(artifact.parts, parts)
        self.assertEqual(artifact.name, name)
        self.assertEqual(artifact.description, description)

    def test_new_artifact_empty_description_if_not_provided(self):
        parts = [Part(root=TextPart(text='Another sample'))]
        name = 'Artifact_No_Desc'
        artifact = new_artifact(parts=parts, name=name)
        self.assertEqual(artifact.description, None)

    def test_new_text_artifact_creates_single_text_part(self):
        text = 'This is a text artifact.'
        name = 'Text_Artifact'
        artifact = new_text_artifact(text=text, name=name)
        self.assertEqual(len(artifact.parts), 1)
        self.assertIsInstance(artifact.parts[0].root, TextPart)

    def test_new_text_artifact_part_contains_provided_text(self):
        text = 'Hello, world!'
        name = 'Greeting_Artifact'
        artifact = new_text_artifact(text=text, name=name)
        self.assertEqual(artifact.parts[0].root.text, text)

    def test_new_text_artifact_assigns_name_description(self):
        text = 'Some content.'
        name = 'Named_Text_Artifact'
        description = 'Description for text artifact.'
        artifact = new_text_artifact(
            text=text, name=name, description=description
        )
        self.assertEqual(artifact.name, name)
        self.assertEqual(artifact.description, description)

    def test_new_data_artifact_creates_single_data_part(self):
        sample_data = {'key': 'value', 'number': 123}
        name = 'Data_Artifact'
        artifact = new_data_artifact(data=sample_data, name=name)
        self.assertEqual(len(artifact.parts), 1)
        self.assertIsInstance(artifact.parts[0].root, DataPart)

    def test_new_data_artifact_part_contains_provided_data(self):
        sample_data = {'content': 'test_data', 'is_valid': True}
        name = 'Structured_Data_Artifact'
        artifact = new_data_artifact(data=sample_data, name=name)
        self.assertIsInstance(artifact.parts[0].root, DataPart)
        # Ensure the 'data' attribute of DataPart is accessed for comparison
        self.assertEqual(artifact.parts[0].root.data, sample_data)

    def test_new_data_artifact_assigns_name_description(self):
        sample_data = {'info': 'some details'}
        name = 'Named_Data_Artifact'
        description = 'Description for data artifact.'
        artifact = new_data_artifact(
            data=sample_data, name=name, description=description
        )
        self.assertEqual(artifact.name, name)
        self.assertEqual(artifact.description, description)


class TestGetArtifactText(unittest.TestCase):
    def test_get_artifact_text_single_part(self):
        # Setup
        artifact = Artifact(
            name='test-artifact',
            parts=[Part(root=TextPart(text='Hello world'))],
            artifact_id='test-artifact-id',
        )

        # Exercise
        result = get_artifact_text(artifact)

        # Verify
        assert result == 'Hello world'

    def test_get_artifact_text_multiple_parts(self):
        # Setup
        artifact = Artifact(
            name='test-artifact',
            parts=[
                Part(root=TextPart(text='First line')),
                Part(root=TextPart(text='Second line')),
                Part(root=TextPart(text='Third line')),
            ],
            artifact_id='test-artifact-id',
        )

        # Exercise
        result = get_artifact_text(artifact)

        # Verify - default delimiter is newline
        assert result == 'First line\nSecond line\nThird line'

    def test_get_artifact_text_custom_delimiter(self):
        # Setup
        artifact = Artifact(
            name='test-artifact',
            parts=[
                Part(root=TextPart(text='First part')),
                Part(root=TextPart(text='Second part')),
                Part(root=TextPart(text='Third part')),
            ],
            artifact_id='test-artifact-id',
        )

        # Exercise
        result = get_artifact_text(artifact, delimiter=' | ')

        # Verify
        assert result == 'First part | Second part | Third part'

    def test_get_artifact_text_empty_parts(self):
        # Setup
        artifact = Artifact(
            name='test-artifact',
            parts=[],
            artifact_id='test-artifact-id',
        )

        # Exercise
        result = get_artifact_text(artifact)

        # Verify
        assert result == ''


class TestArtifactStreamer(unittest.TestCase):
    def setUp(self):
        self.context_id = 'ctx-123'
        self.task_id = 'task-456'
        self.name = 'response'

    def test_stable_artifact_id_across_appends(self):
        streamer = ArtifactStreamer(
            self.context_id, self.task_id, name=self.name
        )
        event1 = streamer.append('Hello ')
        event2 = streamer.append('world')
        self.assertEqual(
            event1.artifact.artifact_id, event2.artifact.artifact_id
        )

    def test_append_returns_correct_event_type(self):
        streamer = ArtifactStreamer(
            self.context_id, self.task_id, name=self.name
        )
        event = streamer.append('chunk')
        self.assertIsInstance(event, TaskArtifactUpdateEvent)

    def test_append_sets_append_true_last_chunk_false(self):
        streamer = ArtifactStreamer(
            self.context_id, self.task_id, name=self.name
        )
        event = streamer.append('chunk')
        self.assertTrue(event.append)
        self.assertFalse(event.last_chunk)

    def test_append_sets_context_and_task_ids(self):
        streamer = ArtifactStreamer(
            self.context_id, self.task_id, name=self.name
        )
        event = streamer.append('chunk')
        self.assertEqual(event.context_id, self.context_id)
        self.assertEqual(event.task_id, self.task_id)

    def test_append_sets_text_content(self):
        streamer = ArtifactStreamer(
            self.context_id, self.task_id, name=self.name
        )
        event = streamer.append('Hello world')
        self.assertEqual(len(event.artifact.parts), 1)
        self.assertEqual(event.artifact.parts[0].root.text, 'Hello world')

    def test_append_sets_artifact_name_and_description(self):
        streamer = ArtifactStreamer(
            self.context_id,
            self.task_id,
            name='my-artifact',
            description='A streamed response',
        )
        event = streamer.append('chunk')
        self.assertEqual(event.artifact.name, 'my-artifact')
        self.assertEqual(event.artifact.description, 'A streamed response')

    def test_finalize_sets_last_chunk_true(self):
        streamer = ArtifactStreamer(
            self.context_id, self.task_id, name=self.name
        )
        event = streamer.finalize('done')
        self.assertTrue(event.append)
        self.assertTrue(event.last_chunk)

    def test_finalize_with_empty_text(self):
        streamer = ArtifactStreamer(
            self.context_id, self.task_id, name=self.name
        )
        event = streamer.finalize()
        self.assertEqual(event.artifact.parts[0].root.text, '')
        self.assertTrue(event.last_chunk)

    def test_finalize_uses_same_artifact_id(self):
        streamer = ArtifactStreamer(
            self.context_id, self.task_id, name=self.name
        )
        append_event = streamer.append('chunk')
        finalize_event = streamer.finalize()
        self.assertEqual(
            append_event.artifact.artifact_id,
            finalize_event.artifact.artifact_id,
        )

    def test_append_after_finalize_raises(self):
        streamer = ArtifactStreamer(
            self.context_id, self.task_id, name=self.name
        )
        streamer.finalize()
        with self.assertRaises(RuntimeError):
            streamer.append('too late')

    def test_double_finalize_raises(self):
        streamer = ArtifactStreamer(
            self.context_id, self.task_id, name=self.name
        )
        streamer.finalize()
        with self.assertRaises(RuntimeError):
            streamer.finalize()

    def test_artifact_id_property(self):
        streamer = ArtifactStreamer(
            self.context_id, self.task_id, name=self.name
        )
        artifact_id = streamer.artifact_id
        self.assertIsInstance(artifact_id, str)
        self.assertTrue(len(artifact_id) > 0)

    @patch('uuid.uuid4')
    def test_artifact_id_from_uuid(self, mock_uuid4):
        mock_uuid = uuid.UUID('12345678-1234-5678-1234-567812345678')
        mock_uuid4.return_value = mock_uuid
        streamer = ArtifactStreamer(
            self.context_id, self.task_id, name=self.name
        )
        self.assertEqual(streamer.artifact_id, str(mock_uuid))

    def test_description_defaults_to_none(self):
        streamer = ArtifactStreamer(
            self.context_id, self.task_id, name=self.name
        )
        event = streamer.append('chunk')
        self.assertIsNone(event.artifact.description)


if __name__ == '__main__':
    unittest.main()
