import uuid

from typing import Any
from unittest.mock import patch

import pytest

from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentCardSignature,
    AgentInterface,
    AgentSkill,
    Artifact,
    Message,
    Part,
    Role,
    SendMessageRequest,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
)
from a2a.utils.errors import UnsupportedOperationError

from a2a.utils.signing import _clean_empty, _canonicalize_agent_card
from a2a.server.tasks.task_manager import append_artifact_to_task


# --- Helper Functions ---
def create_test_message(
    role: Role = Role.ROLE_USER,
    text: str = 'Hello',
    message_id: str = 'msg-123',
) -> Message:
    return Message(
        role=role,
        parts=[Part(text=text)],
        message_id=message_id,
    )


def create_test_task(
    task_id: str = 'task-abc',
    context_id: str = 'session-xyz',
) -> Task:
    return Task(
        id=task_id,
        context_id=context_id,
        status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
    )


SAMPLE_AGENT_CARD: dict[str, Any] = {
    'name': 'Test Agent',
    'description': 'A test agent',
    'supported_interfaces': [
        AgentInterface(
            url='http://localhost',
            protocol_binding='HTTP+JSON',
        )
    ],
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


# Test append_artifact_to_task
def test_append_artifact_to_task():
    # Prepare base task
    task = create_test_task()
    assert task.id == 'task-abc'
    assert task.context_id == 'session-xyz'
    assert task.status.state == TaskState.TASK_STATE_SUBMITTED
    assert len(task.history) == 0  # proto repeated fields are empty, not None
    assert len(task.artifacts) == 0

    # Prepare appending artifact and event
    artifact_1 = Artifact(
        artifact_id='artifact-123', parts=[Part(text='Hello')]
    )
    append_event_1 = TaskArtifactUpdateEvent(
        artifact=artifact_1, append=False, task_id='123', context_id='123'
    )

    # Test adding a new artifact (not appending)
    append_artifact_to_task(task, append_event_1)
    assert len(task.artifacts) == 1
    assert task.artifacts[0].artifact_id == 'artifact-123'
    assert task.artifacts[0].name == ''  # proto default for string
    assert len(task.artifacts[0].parts) == 1
    assert task.artifacts[0].parts[0].text == 'Hello'

    # Test replacing the artifact
    artifact_2 = Artifact(
        artifact_id='artifact-123',
        name='updated name',
        parts=[Part(text='Updated')],
        metadata={'existing_key': 'existing_value'},
    )
    append_event_2 = TaskArtifactUpdateEvent(
        artifact=artifact_2, append=False, task_id='123', context_id='123'
    )
    append_artifact_to_task(task, append_event_2)
    assert len(task.artifacts) == 1  # Should still have one artifact
    assert task.artifacts[0].artifact_id == 'artifact-123'
    assert task.artifacts[0].name == 'updated name'
    assert len(task.artifacts[0].parts) == 1
    assert task.artifacts[0].parts[0].text == 'Updated'
    assert task.artifacts[0].metadata['existing_key'] == 'existing_value'

    # Test appending parts to an existing artifact
    artifact_with_parts = Artifact(
        artifact_id='artifact-123',
        parts=[Part(text='Part 2')],
        metadata={'new_key': 'new_value'},
    )
    append_event_3 = TaskArtifactUpdateEvent(
        artifact=artifact_with_parts,
        append=True,
        task_id='123',
        context_id='123',
    )
    append_artifact_to_task(task, append_event_3)
    assert len(task.artifacts[0].parts) == 2
    assert task.artifacts[0].parts[0].text == 'Updated'
    assert task.artifacts[0].parts[1].text == 'Part 2'
    assert task.artifacts[0].metadata['existing_key'] == 'existing_value'
    assert task.artifacts[0].metadata['new_key'] == 'new_value'

    # Test adding another new artifact
    another_artifact_with_parts = Artifact(
        artifact_id='new_artifact',
        parts=[Part(text='new artifact Part 1')],
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
        artifact_id='artifact-456', parts=[Part(text='Part 1')]
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


def build_text_artifact(text: str, artifact_id: str) -> Artifact:
    return Artifact(artifact_id=artifact_id, parts=[Part(text=text)])


# Test build_text_artifact
def test_build_text_artifact():
    artifact_id = 'text_artifact'
    text = 'This is a sample text'
    artifact = build_text_artifact(text, artifact_id)

    assert artifact.artifact_id == artifact_id
    assert len(artifact.parts) == 1
    assert artifact.parts[0].text == text


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
        '"supportedInterfaces":[{"protocolBinding":"HTTP+JSON","url":"http://localhost"}],'
        '"version":"1.0.0"}'
    )
    result = _canonicalize_agent_card(agent_card)
    assert result == expected_jcs


def test_canonicalize_agent_card_preserves_false_capability():
    """Regression #692: streaming=False must not be stripped from canonical JSON."""
    card = AgentCard(
        **{
            **SAMPLE_AGENT_CARD,
            'capabilities': AgentCapabilities(
                streaming=False,
                push_notifications=True,
            ),
        }
    )
    result = _canonicalize_agent_card(card)
    assert '"streaming":false' in result


@pytest.mark.parametrize(
    'input_val',
    [
        pytest.param({'a': ''}, id='empty-string'),
        pytest.param({'a': []}, id='empty-list'),
        pytest.param({'a': {}}, id='empty-dict'),
        pytest.param({'a': {'b': []}}, id='nested-empty'),
        pytest.param({'a': '', 'b': [], 'c': {}}, id='all-empties'),
        pytest.param({'a': {'b': {'c': ''}}}, id='deeply-nested'),
    ],
)
def test_clean_empty_removes_empties(input_val):
    """_clean_empty removes empty strings, lists, and dicts recursively."""
    assert _clean_empty(input_val) is None


def test_clean_empty_top_level_list_becomes_none():
    """Top-level list that becomes empty after cleaning should return None."""
    assert _clean_empty(['', {}, []]) is None


@pytest.mark.parametrize(
    'input_val,expected',
    [
        pytest.param({'retries': 0}, {'retries': 0}, id='int-zero'),
        pytest.param({'enabled': False}, {'enabled': False}, id='bool-false'),
        pytest.param({'score': 0.0}, {'score': 0.0}, id='float-zero'),
        pytest.param([0, 1, 2], [0, 1, 2], id='zero-in-list'),
        pytest.param([False, True], [False, True], id='false-in-list'),
        pytest.param(
            {'config': {'max_retries': 0, 'name': 'agent'}},
            {'config': {'max_retries': 0, 'name': 'agent'}},
            id='nested-zero',
        ),
    ],
)
def test_clean_empty_preserves_falsy_values(input_val, expected):
    """_clean_empty preserves legitimate falsy values (0, False, 0.0)."""
    assert _clean_empty(input_val) == expected


@pytest.mark.parametrize(
    'input_val,expected',
    [
        pytest.param(
            {'count': 0, 'label': '', 'items': []},
            {'count': 0},
            id='falsy-with-empties',
        ),
        pytest.param(
            {'a': 0, 'b': 'hello', 'c': False, 'd': ''},
            {'a': 0, 'b': 'hello', 'c': False},
            id='mixed-types',
        ),
        pytest.param(
            {'name': 'agent', 'retries': 0, 'tags': [], 'desc': ''},
            {'name': 'agent', 'retries': 0},
            id='realistic-mixed',
        ),
    ],
)
def test_clean_empty_mixed(input_val, expected):
    """_clean_empty handles mixed empty and falsy values correctly."""
    assert _clean_empty(input_val) == expected


def test_clean_empty_does_not_mutate_input():
    """_clean_empty should not mutate the original input object."""
    original = {'a': '', 'b': 1, 'c': {'d': ''}}
    original_copy = {
        'a': '',
        'b': 1,
        'c': {'d': ''},
    }

    _clean_empty(original)

    assert original == original_copy
