"""Tests for vertex_task_converter mappings."""

from vertexai import types as vertexai_types

from a2a.contrib.tasks.vertex_task_converter import (
    to_sdk_artifact,
    to_sdk_message,
    to_stored_artifact,
    to_stored_message,
)
from a2a.types import (
    Artifact,
    DataPart,
    Message,
    Part,
    Role,
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
