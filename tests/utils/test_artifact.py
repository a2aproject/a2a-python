import unittest
import uuid

from unittest.mock import patch

from google.protobuf.struct_pb2 import Struct

from a2a.types.a2a_pb2 import (
    Artifact,
    Part,
    TaskArtifactUpdateEvent,
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
        parts = [Part(text='Sample text')]
        name = 'My Artifact'
        description = 'This is a test artifact.'
        artifact = new_artifact(parts=parts, name=name, description=description)
        assert len(artifact.parts) == len(parts)
        self.assertEqual(artifact.name, name)
        self.assertEqual(artifact.description, description)

    def test_new_artifact_empty_description_if_not_provided(self):
        parts = [Part(text='Another sample')]
        name = 'Artifact_No_Desc'
        artifact = new_artifact(parts=parts, name=name)
        self.assertEqual(artifact.description, '')

    def test_new_text_artifact_creates_single_text_part(self):
        text = 'This is a text artifact.'
        name = 'Text_Artifact'
        artifact = new_text_artifact(text=text, name=name)
        self.assertEqual(len(artifact.parts), 1)
        self.assertTrue(artifact.parts[0].HasField('text'))

    def test_new_text_artifact_part_contains_provided_text(self):
        text = 'Hello, world!'
        name = 'Greeting_Artifact'
        artifact = new_text_artifact(text=text, name=name)
        self.assertEqual(artifact.parts[0].text, text)

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
        self.assertTrue(artifact.parts[0].HasField('data'))

    def test_new_data_artifact_part_contains_provided_data(self):
        sample_data = {'content': 'test_data', 'is_valid': True}
        name = 'Structured_Data_Artifact'
        artifact = new_data_artifact(data=sample_data, name=name)
        self.assertTrue(artifact.parts[0].HasField('data'))
        # Compare via MessageToDict for proto Struct
        from google.protobuf.json_format import MessageToDict

        self.assertEqual(MessageToDict(artifact.parts[0].data), sample_data)

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
            parts=[Part(text='Hello world')],
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
                Part(text='First line'),
                Part(text='Second line'),
                Part(text='Third line'),
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
                Part(text='First part'),
                Part(text='Second part'),
                Part(text='Third part'),
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

    def test_generates_stable_artifact_id(self):
        streamer = ArtifactStreamer(self.context_id, self.task_id)
        e1 = streamer.append('hello ')
        e2 = streamer.append('world')
        self.assertEqual(e1.artifact.artifact_id, e2.artifact.artifact_id)

    def test_uses_explicit_artifact_id(self):
        streamer = ArtifactStreamer(
            self.context_id, self.task_id, artifact_id='my-fixed-id'
        )
        event = streamer.append('chunk')
        self.assertEqual(event.artifact.artifact_id, 'my-fixed-id')

    @patch('a2a.utils.artifact.uuid.uuid4')
    def test_generated_id_comes_from_uuid4(self, mock_uuid4):
        mock_uuid = uuid.UUID('abcdef12-1234-5678-1234-567812345678')
        mock_uuid4.return_value = mock_uuid
        streamer = ArtifactStreamer(self.context_id, self.task_id)
        self.assertEqual(streamer._artifact_id, str(mock_uuid))

    def test_default_name_is_response(self):
        streamer = ArtifactStreamer(self.context_id, self.task_id)
        event = streamer.append('text')
        self.assertEqual(event.artifact.name, 'response')

    def test_custom_name(self):
        streamer = ArtifactStreamer(
            self.context_id, self.task_id, name='summary'
        )
        event = streamer.append('text')
        self.assertEqual(event.artifact.name, 'summary')

    def test_append_returns_task_artifact_update_event(self):
        streamer = ArtifactStreamer(self.context_id, self.task_id)
        event = streamer.append('chunk')
        self.assertIsInstance(event, TaskArtifactUpdateEvent)

    def test_append_sets_correct_context_and_task(self):
        streamer = ArtifactStreamer(self.context_id, self.task_id)
        event = streamer.append('chunk')
        self.assertEqual(event.context_id, self.context_id)
        self.assertEqual(event.task_id, self.task_id)

    def test_append_sets_append_true_last_chunk_false(self):
        streamer = ArtifactStreamer(self.context_id, self.task_id)
        event = streamer.append('chunk')
        self.assertTrue(event.append)
        self.assertFalse(event.last_chunk)

    def test_append_creates_single_text_part(self):
        streamer = ArtifactStreamer(self.context_id, self.task_id)
        event = streamer.append('hello')
        self.assertEqual(len(event.artifact.parts), 1)
        self.assertTrue(event.artifact.parts[0].HasField('text'))
        self.assertEqual(event.artifact.parts[0].text, 'hello')

    def test_finalize_returns_task_artifact_update_event(self):
        streamer = ArtifactStreamer(self.context_id, self.task_id)
        event = streamer.finalize()
        self.assertIsInstance(event, TaskArtifactUpdateEvent)

    def test_finalize_sets_append_true_last_chunk_true(self):
        streamer = ArtifactStreamer(self.context_id, self.task_id)
        event = streamer.finalize()
        self.assertTrue(event.append)
        self.assertTrue(event.last_chunk)

    def test_finalize_has_empty_parts(self):
        streamer = ArtifactStreamer(self.context_id, self.task_id)
        event = streamer.finalize()
        self.assertEqual(len(event.artifact.parts), 0)

    def test_finalize_uses_same_artifact_id_as_append(self):
        streamer = ArtifactStreamer(self.context_id, self.task_id)
        append_event = streamer.append('text')
        finalize_event = streamer.finalize()
        self.assertEqual(
            append_event.artifact.artifact_id,
            finalize_event.artifact.artifact_id,
        )

    def test_multiple_appends_all_share_artifact_id(self):
        streamer = ArtifactStreamer(self.context_id, self.task_id)
        events = [streamer.append(f'chunk-{i}') for i in range(5)]
        ids = {e.artifact.artifact_id for e in events}
        self.assertEqual(len(ids), 1)

    def test_multiple_appends_carry_distinct_text(self):
        streamer = ArtifactStreamer(self.context_id, self.task_id)
        texts = ['Hello, ', 'world', '!']
        events = [streamer.append(t) for t in texts]
        result_texts = [e.artifact.parts[0].text for e in events]
        self.assertEqual(result_texts, texts)


if __name__ == '__main__':
    unittest.main()
