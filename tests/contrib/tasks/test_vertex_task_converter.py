"""Tests for vertex_task_converter mappings."""

import base64

import pytest


pytest.importorskip(
    'vertexai', reason='Vertex Task Converter tests require vertexai'
)
from google.genai import types as genai_types
from vertexai import types as vertexai_types

from a2a.contrib.tasks.vertex_task_converter import (
    to_sdk_artifact,
    to_sdk_message,
    to_sdk_part,
    to_sdk_task,
    to_sdk_task_state,
    to_stored_artifact,
    to_stored_message,
    to_stored_part,
    to_stored_task,
    to_stored_task_state,
)
from a2a.types import (
    Artifact,
    DataPart,
    FilePart,
    FileWithBytes,
    FileWithUri,
    Message,
    Part,
    Role,
    Task,
    TaskState,
    TaskStatus,
    TextPart,
)


def test_artifact_conversion_symmetry() -> None:
    """Test converting an Artifact to TaskArtifact and back restores everything."""
    original_artifact = Artifact(
        artifact_id='art123',
        name='My cool artifact',
        description='A very interesting description',
        extensions=['ext1', 'ext2'],
        metadata={'custom': 'value'},
        parts=[
            Part(
                root=TextPart(
                    text='hello', metadata={'part_meta': 'hello_meta'}
                )
            ),
            Part(root=DataPart(data={'foo': 'bar'})),  # no metadata
        ],
    )

    stored = to_stored_artifact(original_artifact)
    assert isinstance(stored, vertexai_types.TaskArtifact)

    # ensure it was populated correctly
    assert stored.display_name == 'My cool artifact'
    assert stored.description == 'A very interesting description'
    assert stored.metadata['__vertex_compat_v'] == 1.0

    restored_artifact = to_sdk_artifact(stored)

    assert restored_artifact.artifact_id == original_artifact.artifact_id
    assert restored_artifact.name == original_artifact.name
    assert restored_artifact.description == original_artifact.description
    assert restored_artifact.extensions == original_artifact.extensions
    assert restored_artifact.metadata == original_artifact.metadata

    assert len(restored_artifact.parts) == 2
    assert isinstance(restored_artifact.parts[0].root, TextPart)
    assert restored_artifact.parts[0].root.text == 'hello'
    assert restored_artifact.parts[0].root.metadata == {
        'part_meta': 'hello_meta'
    }

    assert isinstance(restored_artifact.parts[1].root, DataPart)
    assert restored_artifact.parts[1].root.data == {'foo': 'bar'}
    assert restored_artifact.parts[1].root.metadata is None


def test_message_conversion_symmetry() -> None:
    """Test converting a Message to TaskMessage and back restores everything."""
    original_message = Message(
        message_id='msg456',
        role=Role.agent,
        context_id='ctx1',
        task_id='tsk1',
        reference_task_ids=['tsk2', 'tsk3'],
        extensions=['ext_msg'],
        metadata={'msg_meta': 42},
        parts=[
            Part(root=TextPart(text='message text')),
        ],
    )

    stored = to_stored_message(original_message)
    assert stored is not None
    assert isinstance(stored, vertexai_types.TaskMessage)

    assert stored.message_id == 'msg456'
    assert stored.role == 'agent'
    assert stored.metadata['__vertex_compat_v'] == 1.0

    restored_message = to_sdk_message(stored)
    assert restored_message is not None

    assert restored_message.message_id == original_message.message_id
    assert restored_message.role == original_message.role
    # context_id and task_id are not serialized via Message metadata in Go implementation but via Task,
    # but reference_task_ids and extensions ARE part of Message metadata.
    assert (
        restored_message.reference_task_ids
        == original_message.reference_task_ids
    )
    assert restored_message.extensions == original_message.extensions
    assert restored_message.metadata == original_message.metadata

    assert len(restored_message.parts) == 1
    assert isinstance(restored_message.parts[0].root, TextPart)
    assert restored_message.parts[0].root.text == 'message text'
    assert restored_message.parts[0].root.metadata is None


def test_to_stored_part_unsupported() -> None:
    part = Part.model_construct(
        root=Task(  # type: ignore[arg-type]
            id='invalid-part',
            context_id='ctx',
            status=TaskStatus(state=TaskState.submitted),
            history=[],
        )
    )
    with pytest.raises(ValueError, match='Unsupported part type'):
        to_stored_part(part)


def test_to_sdk_part_text() -> None:
    stored_part = genai_types.Part(text='hello back')
    sdk_part = to_sdk_part(stored_part)
    assert isinstance(sdk_part.root, TextPart)
    assert sdk_part.root.text == 'hello back'


def test_to_sdk_part_inline_data() -> None:
    stored_part = genai_types.Part(
        inline_data=genai_types.Blob(
            mime_type='application/json',
            data=b'{"key": "val"}',
        )
    )
    sdk_part = to_sdk_part(stored_part)
    assert isinstance(sdk_part.root, FilePart)
    assert isinstance(sdk_part.root.file, FileWithBytes)
    expected_b64 = base64.b64encode(b'{"key": "val"}').decode('utf-8')
    assert sdk_part.root.file.mime_type == 'application/json'
    assert sdk_part.root.file.bytes == expected_b64


def test_to_sdk_part_file_data() -> None:
    stored_part = genai_types.Part(
        file_data=genai_types.FileData(
            mime_type='image/jpeg',
            file_uri='gs://bucket/image.jpg',
        )
    )
    sdk_part = to_sdk_part(stored_part)
    assert isinstance(sdk_part.root, FilePart)
    assert isinstance(sdk_part.root.file, FileWithUri)
    assert sdk_part.root.file.mime_type == 'image/jpeg'
    assert sdk_part.root.file.uri == 'gs://bucket/image.jpg'


def test_to_sdk_part_unsupported() -> None:
    stored_part = genai_types.Part()
    with pytest.raises(ValueError, match='Unsupported part:'):
        to_sdk_part(stored_part)


def test_to_stored_artifact() -> None:
    sdk_artifact = Artifact(
        artifact_id='art-123',
        parts=[Part(root=TextPart(text='part_1'))],
    )
    stored_artifact = to_stored_artifact(sdk_artifact)
    assert stored_artifact.artifact_id == 'art-123'
    assert len(stored_artifact.parts) == 1
    assert stored_artifact.parts[0].text == 'part_1'


def test_to_sdk_artifact() -> None:
    stored_artifact = vertexai_types.TaskArtifact(
        artifact_id='art-456',
        parts=[genai_types.Part(text='part_2')],
    )
    sdk_artifact = to_sdk_artifact(stored_artifact)
    assert sdk_artifact.artifact_id == 'art-456'
    assert len(sdk_artifact.parts) == 1
    assert isinstance(sdk_artifact.parts[0].root, TextPart)
    assert sdk_artifact.parts[0].root.text == 'part_2'


def test_to_stored_task() -> None:
    sdk_task = Task(
        id='task-1',
        context_id='ctx-1',
        status=TaskStatus(state=TaskState.working),
        metadata={'foo': 'bar'},
        artifacts=[
            Artifact(
                artifact_id='art-1',
                parts=[Part(root=TextPart(text='stuff'))],
            )
        ],
        history=[],
    )
    stored_task = to_stored_task(sdk_task)
    assert stored_task.context_id == 'ctx-1'
    assert stored_task.metadata == {'foo': 'bar'}
    assert stored_task.state == vertexai_types.A2aTaskState.WORKING
    assert stored_task.output is not None
    assert stored_task.output.artifacts is not None
    assert len(stored_task.output.artifacts) == 1
    assert stored_task.output.artifacts[0].artifact_id == 'art-1'


def test_to_sdk_task() -> None:
    stored_task = vertexai_types.A2aTask(
        name='projects/123/locations/us-central1/agentEngines/456/tasks/task-2',
        context_id='ctx-2',
        state=vertexai_types.A2aTaskState.COMPLETED,
        metadata={'a': 'b'},
        output=vertexai_types.TaskOutput(
            artifacts=[
                vertexai_types.TaskArtifact(
                    artifact_id='art-2',
                    parts=[genai_types.Part(text='result')],
                )
            ]
        ),
    )
    sdk_task = to_sdk_task(stored_task)
    assert sdk_task.id == 'task-2'
    assert sdk_task.context_id == 'ctx-2'
    assert sdk_task.status.state == TaskState.completed
    assert sdk_task.metadata == {'a': 'b'}
    assert sdk_task.history == []
    assert sdk_task.artifacts is not None
    assert len(sdk_task.artifacts) == 1
    assert sdk_task.artifacts[0].artifact_id == 'art-2'
    assert isinstance(sdk_task.artifacts[0].parts[0].root, TextPart)
    assert sdk_task.artifacts[0].parts[0].root.text == 'result'


def test_to_sdk_task_no_output() -> None:
    stored_task = vertexai_types.A2aTask(
        name='tasks/task-3',
        context_id='ctx-3',
        state=vertexai_types.A2aTaskState.SUBMITTED,
        metadata=None,
    )
    sdk_task = to_sdk_task(stored_task)
    assert sdk_task.id == 'task-3'
    assert sdk_task.metadata == {}
    assert sdk_task.artifacts == []


def test_sdk_task_state_conversion_round_trip() -> None:
    for state in TaskState:
        stored_state = to_stored_task_state(state)
        round_trip_state = to_sdk_task_state(stored_state)
        assert round_trip_state == state


def test_sdk_part_text_conversion_round_trip() -> None:
    sdk_part = Part(root=TextPart(text='hello world'))
    stored_part = to_stored_part(sdk_part)
    round_trip_sdk_part = to_sdk_part(stored_part)
    assert round_trip_sdk_part == sdk_part


def test_sdk_part_data_conversion_round_trip() -> None:
    # A DataPart is converted to `inline_data` in Vertex AI, which lacks the original
    # `DataPart` vs `FilePart` distinction. When reading it back from the stored
    # protocol format, it becomes a `FilePart` with base64-encoded `FileWithBytes`
    # and `mime_type="application/json"`.
    sdk_part = Part(root=DataPart(data={'key': 'value'}))
    stored_part = to_stored_part(sdk_part)
    round_trip_sdk_part = to_sdk_part(stored_part)

    expected_b64 = base64.b64encode(b'{"key": "value"}').decode('utf-8')
    assert round_trip_sdk_part == Part(
        root=FilePart(
            file=FileWithBytes(
                bytes=expected_b64,
                mime_type='application/json',
            )
        )
    )


def test_sdk_part_file_bytes_conversion_round_trip() -> None:
    encoded_b64 = base64.b64encode(b'test data').decode('utf-8')
    sdk_part = Part(
        root=FilePart(
            file=FileWithBytes(
                bytes=encoded_b64,
                mime_type='text/plain',
            )
        )
    )
    stored_part = to_stored_part(sdk_part)
    round_trip_sdk_part = to_sdk_part(stored_part)
    assert round_trip_sdk_part == sdk_part


def test_sdk_part_file_uri_conversion_round_trip() -> None:
    sdk_part = Part(
        root=FilePart(
            file=FileWithUri(
                uri='gs://test-bucket/file.txt',
                mime_type='text/plain',
            )
        )
    )
    stored_part = to_stored_part(sdk_part)
    round_trip_sdk_part = to_sdk_part(stored_part)
    assert round_trip_sdk_part == sdk_part


def test_sdk_artifact_conversion_round_trip() -> None:
    sdk_artifact = Artifact(
        artifact_id='art-123',
        parts=[Part(root=TextPart(text='part_1'))],
    )
    stored_artifact = to_stored_artifact(sdk_artifact)
    round_trip_sdk_artifact = to_sdk_artifact(stored_artifact)
    assert round_trip_sdk_artifact == sdk_artifact


def test_sdk_task_conversion_round_trip() -> None:
    sdk_task = Task(
        id='task-1',
        context_id='ctx-1',
        status=TaskStatus(state=TaskState.working),
        metadata={'foo': 'bar'},
        artifacts=[
            Artifact(
                artifact_id='art-1',
                parts=[Part(root=TextPart(text='stuff'))],
            )
        ],
        history=[
            # History is not yet implemented and later will be supported
            # via events.
        ],
    )
    stored_task = to_stored_task(sdk_task)
    # Simulate Vertex storing the ID in the fully qualified resource name.
    # The task ID during creation gets appended to the parent name.
    stored_task.name = (
        f'projects/p/locations/l/agentEngines/e/tasks/{sdk_task.id}'
    )

    round_trip_sdk_task = to_sdk_task(stored_task)

    assert round_trip_sdk_task.id == sdk_task.id
    assert round_trip_sdk_task.context_id == sdk_task.context_id
    assert round_trip_sdk_task.status == sdk_task.status
    assert round_trip_sdk_task.metadata == sdk_task.metadata
    assert round_trip_sdk_task.artifacts == sdk_task.artifacts
    assert round_trip_sdk_task.history == []
