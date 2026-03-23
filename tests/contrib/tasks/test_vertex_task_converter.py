import base64

import pytest


pytest.importorskip(
    'vertexai', reason='Vertex Task Converter tests require vertexai'
)
from vertexai import types as vertexai_types
from google.genai import types as genai_types
from a2a.contrib.tasks.vertex_task_converter import (
    to_sdk_artifact,
    to_sdk_part,
    to_sdk_task,
    to_sdk_task_state,
    to_stored_artifact,
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
    Part,
    Task,
    TaskState,
    TaskStatus,
    TextPart,
)


def test_to_sdk_task_state() -> None:
    assert (
        to_sdk_task_state(vertexai_types.A2aTaskState.STATE_UNSPECIFIED)
        == TaskState.unknown
    )
    assert (
        to_sdk_task_state(vertexai_types.A2aTaskState.SUBMITTED)
        == TaskState.submitted
    )
    assert (
        to_sdk_task_state(vertexai_types.A2aTaskState.WORKING)
        == TaskState.working
    )
    assert (
        to_sdk_task_state(vertexai_types.A2aTaskState.COMPLETED)
        == TaskState.completed
    )
    assert (
        to_sdk_task_state(vertexai_types.A2aTaskState.CANCELLED)
        == TaskState.canceled
    )
    assert (
        to_sdk_task_state(vertexai_types.A2aTaskState.FAILED)
        == TaskState.failed
    )
    assert (
        to_sdk_task_state(vertexai_types.A2aTaskState.REJECTED)
        == TaskState.rejected
    )
    assert (
        to_sdk_task_state(vertexai_types.A2aTaskState.INPUT_REQUIRED)
        == TaskState.input_required
    )
    assert (
        to_sdk_task_state(vertexai_types.A2aTaskState.AUTH_REQUIRED)
        == TaskState.auth_required
    )
    assert to_sdk_task_state(999) == TaskState.unknown  # type: ignore


def test_to_stored_task_state() -> None:
    assert (
        to_stored_task_state(TaskState.unknown)
        == vertexai_types.A2aTaskState.STATE_UNSPECIFIED
    )
    assert (
        to_stored_task_state(TaskState.submitted)
        == vertexai_types.A2aTaskState.SUBMITTED
    )
    assert (
        to_stored_task_state(TaskState.working)
        == vertexai_types.A2aTaskState.WORKING
    )
    assert (
        to_stored_task_state(TaskState.completed)
        == vertexai_types.A2aTaskState.COMPLETED
    )
    assert (
        to_stored_task_state(TaskState.canceled)
        == vertexai_types.A2aTaskState.CANCELLED
    )
    assert (
        to_stored_task_state(TaskState.failed)
        == vertexai_types.A2aTaskState.FAILED
    )
    assert (
        to_stored_task_state(TaskState.rejected)
        == vertexai_types.A2aTaskState.REJECTED
    )
    assert (
        to_stored_task_state(TaskState.input_required)
        == vertexai_types.A2aTaskState.INPUT_REQUIRED
    )
    assert (
        to_stored_task_state(TaskState.auth_required)
        == vertexai_types.A2aTaskState.AUTH_REQUIRED
    )


def test_to_stored_part_text() -> None:
    sdk_part = Part(root=TextPart(text='hello world'))
    stored_part = to_stored_part(sdk_part)
    assert stored_part.text == 'hello world'
    assert not stored_part.inline_data
    assert not stored_part.file_data


def test_to_stored_part_data() -> None:
    sdk_part = Part(root=DataPart(data={'key': 'value'}))
    stored_part = to_stored_part(sdk_part)
    assert stored_part.inline_data is not None
    assert stored_part.inline_data.mime_type == 'application/json'
    assert stored_part.inline_data.data == b'{"key": "value"}'


def test_to_stored_part_file_bytes() -> None:
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
    assert stored_part.inline_data is not None
    assert stored_part.inline_data.mime_type == 'text/plain'
    assert stored_part.inline_data.data == b'test data'


def test_to_stored_part_file_uri() -> None:
    sdk_part = Part(
        root=FilePart(
            file=FileWithUri(
                uri='gs://test-bucket/file.txt',
                mime_type='text/plain',
            )
        )
    )
    stored_part = to_stored_part(sdk_part)
    assert stored_part.file_data is not None
    assert stored_part.file_data.mime_type == 'text/plain'
    assert stored_part.file_data.file_uri == 'gs://test-bucket/file.txt'


def test_to_stored_part_unsupported() -> None:
    class BadPart:
        pass

    part = Part(root=TextPart(text='t'))
    part.root = BadPart()  # type: ignore
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
