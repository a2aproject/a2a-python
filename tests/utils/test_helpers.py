import uuid

from typing import Any
from unittest.mock import patch

import pytest

from a2a.types import (
    Artifact,
    AgentCard,
    AgentCardSignature,
    AgentCapabilities,
    AgentSkill,
    Message,
    MessageSendParams,
    Part,
    Role,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TextPart,
)
from a2a.utils.errors import ServerError
from a2a.utils.helpers import (
    append_artifact_to_task,
    are_modalities_compatible,
    build_text_artifact,
    create_task_obj,
    validate,
    canonicalize_agent_card,
)


# --- Helper Data ---
TEXT_PART_DATA: dict[str, Any] = {'type': 'text', 'text': 'Hello'}

MINIMAL_MESSAGE_USER: dict[str, Any] = {
    'role': 'user',
    'parts': [TEXT_PART_DATA],
    'message_id': 'msg-123',
    'type': 'message',
}

MINIMAL_TASK_STATUS: dict[str, Any] = {'state': 'submitted'}

MINIMAL_TASK: dict[str, Any] = {
    'id': 'task-abc',
    'context_id': 'session-xyz',
    'status': MINIMAL_TASK_STATUS,
    'type': 'task',
}

SAMPLE_AGENT_CARD: dict[str, Any] = {
    'name': 'Test Agent',
    'description': 'A test agent',
    'url': 'http://localhost',
    'version': '1.0.0',
    'capabilities': AgentCapabilities(
        streaming=None,
        push_notifications=True,
    ),
    'default_input_modes': ['text/plain'],
    'default_output_modes': ['text/plain'],
    'documentation_url': None,
    'icon_url': '',
    'skills': [
        AgentSkill(
            id='skill1',
            name='Test Skill',
            description='A test skill',
            tags=['test'],
        )
    ],
    'signatures': [
        AgentCardSignature(
            protected='protected_header', signature='test_signature'
        )
    ],
}


# Test create_task_obj
def test_create_task_obj():
    message = Message(**MINIMAL_MESSAGE_USER)
    send_params = MessageSendParams(message=message)

    task = create_task_obj(send_params)
    assert task.id is not None
    assert task.context_id == message.context_id
    assert task.status.state == TaskState.submitted
    assert len(task.history) == 1
    assert task.history[0] == message


def test_create_task_obj_generates_context_id():
    """Test that create_task_obj generates context_id if not present and uses it for the task."""
    # Message without context_id
    message_no_context_id = Message(
        role=Role.user,
        parts=[Part(root=TextPart(text='test'))],
        message_id='msg-no-ctx',
        task_id='task-from-msg',  # Provide a task_id to differentiate from generated task.id
    )
    send_params = MessageSendParams(message=message_no_context_id)

    # Ensure message.context_id is None initially
    assert send_params.message.context_id is None

    known_task_uuid = uuid.UUID('11111111-1111-1111-1111-111111111111')
    known_context_uuid = uuid.UUID('22222222-2222-2222-2222-222222222222')

    # Patch uuid.uuid4 to return specific UUIDs in sequence
    # The first call will be for message.context_id (if None), the second for task.id.
    with patch(
        'a2a.utils.helpers.uuid4',
        side_effect=[known_context_uuid, known_task_uuid],
    ) as mock_uuid4:
        task = create_task_obj(send_params)

    # Assert that uuid4 was called twice (once for context_id, once for task.id)
    assert mock_uuid4.call_count == 2

    # Assert that message.context_id was set to the first generated UUID
    assert send_params.message.context_id == str(known_context_uuid)

    # Assert that task.context_id is the same generated UUID
    assert task.context_id == str(known_context_uuid)

    # Assert that task.id is the second generated UUID
    assert task.id == str(known_task_uuid)

    # Ensure the original message in history also has the updated context_id
    assert len(task.history) == 1
    assert task.history[0].context_id == str(known_context_uuid)


# Test append_artifact_to_task
def test_append_artifact_to_task():
    # Prepare base task
    task = Task(**MINIMAL_TASK)
    assert task.id == 'task-abc'
    assert task.context_id == 'session-xyz'
    assert task.status.state == TaskState.submitted
    assert task.history is None
    assert task.artifacts is None
    assert task.metadata is None

    # Prepare appending artifact and event
    artifact_1 = Artifact(
        artifact_id='artifact-123', parts=[Part(root=TextPart(text='Hello'))]
    )
    append_event_1 = TaskArtifactUpdateEvent(
        artifact=artifact_1, append=False, task_id='123', context_id='123'
    )

    # Test adding a new artifact (not appending)
    append_artifact_to_task(task, append_event_1)
    assert len(task.artifacts) == 1
    assert task.artifacts[0].artifact_id == 'artifact-123'
    assert task.artifacts[0].name is None
    assert len(task.artifacts[0].parts) == 1
    assert task.artifacts[0].parts[0].root.text == 'Hello'

    # Test replacing the artifact
    artifact_2 = Artifact(
        artifact_id='artifact-123',
        name='updated name',
        parts=[Part(root=TextPart(text='Updated'))],
    )
    append_event_2 = TaskArtifactUpdateEvent(
        artifact=artifact_2, append=False, task_id='123', context_id='123'
    )
    append_artifact_to_task(task, append_event_2)
    assert len(task.artifacts) == 1  # Should still have one artifact
    assert task.artifacts[0].artifact_id == 'artifact-123'
    assert task.artifacts[0].name == 'updated name'
    assert len(task.artifacts[0].parts) == 1
    assert task.artifacts[0].parts[0].root.text == 'Updated'

    # Test appending parts to an existing artifact
    artifact_with_parts = Artifact(
        artifact_id='artifact-123', parts=[Part(root=TextPart(text='Part 2'))]
    )
    append_event_3 = TaskArtifactUpdateEvent(
        artifact=artifact_with_parts,
        append=True,
        task_id='123',
        context_id='123',
    )
    append_artifact_to_task(task, append_event_3)
    assert len(task.artifacts[0].parts) == 2
    assert task.artifacts[0].parts[0].root.text == 'Updated'
    assert task.artifacts[0].parts[1].root.text == 'Part 2'

    # Test adding another new artifact
    another_artifact_with_parts = Artifact(
        artifact_id='new_artifact',
        parts=[Part(root=TextPart(text='new artifact Part 1'))],
    )
    append_event_4 = TaskArtifactUpdateEvent(
        artifact=another_artifact_with_parts,
        append=False,
        task_id='123',
        context_id='123',
    )
    append_artifact_to_task(task, append_event_4)
    assert len(task.artifacts) == 2
    assert task.artifacts[0].artifact_id == 'artifact-123'
    assert task.artifacts[1].artifact_id == 'new_artifact'
    assert len(task.artifacts[0].parts) == 2
    assert len(task.artifacts[1].parts) == 1

    # Test appending part to a task that does not have a matching artifact
    non_existing_artifact_with_parts = Artifact(
        artifact_id='artifact-456', parts=[Part(root=TextPart(text='Part 1'))]
    )
    append_event_5 = TaskArtifactUpdateEvent(
        artifact=non_existing_artifact_with_parts,
        append=True,
        task_id='123',
        context_id='123',
    )
    append_artifact_to_task(task, append_event_5)
    assert len(task.artifacts) == 2
    assert len(task.artifacts[0].parts) == 2
    assert len(task.artifacts[1].parts) == 1


# Test build_text_artifact
def test_build_text_artifact():
    artifact_id = 'text_artifact'
    text = 'This is a sample text'
    artifact = build_text_artifact(text, artifact_id)

    assert artifact.artifact_id == artifact_id
    assert len(artifact.parts) == 1
    assert artifact.parts[0].root.text == text


# Test validate decorator
def test_validate_decorator():
    class TestClass:
        condition = True

        @validate(lambda self: self.condition, 'Condition not met')
        def test_method(self) -> str:
            return 'Success'

    obj = TestClass()

    # Test passing condition
    assert obj.test_method() == 'Success'

    # Test failing condition
    obj.condition = False
    with pytest.raises(ServerError) as exc_info:
        obj.test_method()
    assert 'Condition not met' in str(exc_info.value)


# Tests for are_modalities_compatible
def test_are_modalities_compatible_client_none():
    assert (
        are_modalities_compatible(
            client_output_modes=None, server_output_modes=['text/plain']
        )
        is True
    )


def test_are_modalities_compatible_client_empty():
    assert (
        are_modalities_compatible(
            client_output_modes=[], server_output_modes=['text/plain']
        )
        is True
    )


def test_are_modalities_compatible_server_none():
    assert (
        are_modalities_compatible(
            server_output_modes=None, client_output_modes=['text/plain']
        )
        is True
    )


def test_are_modalities_compatible_server_empty():
    assert (
        are_modalities_compatible(
            server_output_modes=[], client_output_modes=['text/plain']
        )
        is True
    )


def test_are_modalities_compatible_common_mode():
    assert (
        are_modalities_compatible(
            server_output_modes=['text/plain', 'application/json'],
            client_output_modes=['application/json', 'image/png'],
        )
        is True
    )


def test_are_modalities_compatible_no_common_modes():
    assert (
        are_modalities_compatible(
            server_output_modes=['text/plain'],
            client_output_modes=['application/json'],
        )
        is False
    )


def test_are_modalities_compatible_exact_match():
    assert (
        are_modalities_compatible(
            server_output_modes=['text/plain'],
            client_output_modes=['text/plain'],
        )
        is True
    )


def test_are_modalities_compatible_server_more_but_common():
    assert (
        are_modalities_compatible(
            server_output_modes=['text/plain', 'image/jpeg'],
            client_output_modes=['text/plain'],
        )
        is True
    )


def test_are_modalities_compatible_client_more_but_common():
    assert (
        are_modalities_compatible(
            server_output_modes=['text/plain'],
            client_output_modes=['text/plain', 'image/jpeg'],
        )
        is True
    )


def test_are_modalities_compatible_both_none():
    assert (
        are_modalities_compatible(
            server_output_modes=None, client_output_modes=None
        )
        is True
    )


def test_are_modalities_compatible_both_empty():
    assert (
        are_modalities_compatible(
            server_output_modes=[], client_output_modes=[]
        )
        is True
    )


def test_canonicalize_agent_card():
    """Test canonicalize_agent_card with defaults, optionals, and exceptions.

    - extensions is omitted as it's not set and optional.
    - protocolVersion is included because it's always added by canonicalize_agent_card.
    - signatures should be omitted.
    """
    agent_card = AgentCard(**SAMPLE_AGENT_CARD)
    expected_jcs = (
        '{"capabilities":{"pushNotifications":true},'
        '"defaultInputModes":["text/plain"],"defaultOutputModes":["text/plain"],'
        '"description":"A test agent","name":"Test Agent",'
        '"skills":[{"description":"A test skill","id":"skill1","name":"Test Skill","tags":["test"]}],'
        '"url":"http://localhost","version":"1.0.0"}'
    )
    result = canonicalize_agent_card(agent_card)
    assert result == expected_jcs
