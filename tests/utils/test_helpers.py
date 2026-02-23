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
from a2a.utils.errors import ServerError
from a2a.utils.helpers import (
    _clean_empty,
    append_artifact_to_task,
    are_modalities_compatible,
    build_text_artifact,
    canonicalize_agent_card,
    create_task_obj,
    validate,
)


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


# Test create_task_obj
def test_create_task_obj():
    message = create_test_message()
    message.context_id = 'test-context'  # Set context_id to test it's preserved
    send_params = SendMessageRequest(message=message)

    task = create_task_obj(send_params)
    assert task.id is not None
    assert task.context_id == message.context_id
    assert task.status.state == TaskState.TASK_STATE_SUBMITTED
    assert len(task.history) == 1
    assert task.history[0] == message


def test_create_task_obj_generates_context_id():
    """Test that create_task_obj generates context_id if not present and uses it for the task."""
    # Message without context_id
    message_no_context_id = Message(
        role=Role.ROLE_USER,
        parts=[Part(text='test')],
        message_id='msg-no-ctx',
        task_id='task-from-msg',  # Provide a task_id to differentiate from generated task.id
    )
    send_params = SendMessageRequest(message=message_no_context_id)

    # Ensure message.context_id is empty initially (proto default is empty string)
    assert send_params.message.context_id == ''

    known_task_uuid = uuid.UUID('11111111-1111-1111-1111-111111111111')
    known_context_uuid = uuid.UUID('22222222-2222-2222-2222-222222222222')

    # Patch uuid.uuid4 to return specific UUIDs in sequence
    # The first call will be for message.context_id (if empty), the second for task.id.
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

    # Test appending parts to an existing artifact
    artifact_with_parts = Artifact(
        artifact_id='artifact-123', parts=[Part(text='Part 2')]
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


# Test build_text_artifact
def test_build_text_artifact():
    artifact_id = 'text_artifact'
    text = 'This is a sample text'
    artifact = build_text_artifact(text, artifact_id)

    assert artifact.artifact_id == artifact_id
    assert len(artifact.parts) == 1
    assert artifact.parts[0].text == text


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
        '"supportedInterfaces":[{"protocolBinding":"HTTP+JSON","url":"http://localhost"}],'
        '"version":"1.0.0"}'
    )
    result = canonicalize_agent_card(agent_card)
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
    result = canonicalize_agent_card(card)
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
