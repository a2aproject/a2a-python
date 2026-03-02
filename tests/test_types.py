"""Tests for protobuf-based A2A types.

This module tests the proto-generated types from a2a_pb2, using protobuf
patterns like ParseDict, proto constructors, and MessageToDict.
"""

from typing import Any

import pytest
from google.protobuf.json_format import MessageToDict, ParseDict
from google.protobuf.struct_pb2 import Struct, Value

from a2a.types.a2a_pb2 import (
    AgentCapabilities,
    AgentInterface,
    AgentCard,
    AgentProvider,
    AgentSkill,
    APIKeySecurityScheme,
    Artifact,
    CancelTaskRequest,
    CreateTaskPushNotificationConfigRequest,
    GetTaskPushNotificationConfigRequest,
    GetTaskRequest,
    Message,
    Part,
    PushNotificationConfig,
    Role,
    SecurityScheme,
    SendMessageRequest,
    CreateTaskPushNotificationConfigRequest,
    SubscribeToTaskRequest,
    Task,
    TaskPushNotificationConfig,
    TaskState,
    TaskStatus,
)


# --- Helper Data ---

MINIMAL_AGENT_SKILL: dict[str, Any] = {
    'id': 'skill-123',
    'name': 'Recipe Finder',
    'description': 'Finds recipes',
    'tags': ['cooking'],
}

FULL_AGENT_SKILL: dict[str, Any] = {
    'id': 'skill-123',
    'name': 'Recipe Finder',
    'description': 'Finds recipes',
    'tags': ['cooking', 'food'],
    'examples': ['Find me a pasta recipe'],
    'inputModes': ['text/plain'],
    'outputModes': ['application/json'],
}

MINIMAL_AGENT_CARD: dict[str, Any] = {
    'capabilities': {},
    'defaultInputModes': ['text/plain'],
    'defaultOutputModes': ['application/json'],
    'description': 'Test Agent',
    'name': 'TestAgent',
    'skills': [MINIMAL_AGENT_SKILL],
    'supportedInterfaces': [
        {'url': 'http://example.com/agent', 'protocolBinding': 'HTTP+JSON'}
    ],
    'version': '1.0',
}


# --- Test Agent Types ---


def test_agent_capabilities():
    """Test AgentCapabilities proto construction."""
    # Empty capabilities
    caps = AgentCapabilities()
    assert caps.streaming is False  # Proto default
    assert caps.push_notifications is False

    # Full capabilities
    caps_full = AgentCapabilities(
        push_notifications=True,
        streaming=True,
    )
    assert caps_full.push_notifications is True
    assert caps_full.streaming is True


def test_agent_provider():
    """Test AgentProvider proto construction."""
    provider = AgentProvider(
        organization='Test Org',
        url='http://test.org',
    )
    assert provider.organization == 'Test Org'
    assert provider.url == 'http://test.org'


def test_agent_skill():
    """Test AgentSkill proto construction and ParseDict."""
    # Direct construction
    skill = AgentSkill(
        id='skill-123',
        name='Recipe Finder',
        description='Finds recipes',
        tags=['cooking'],
    )
    assert skill.id == 'skill-123'
    assert skill.name == 'Recipe Finder'
    assert skill.description == 'Finds recipes'
    assert list(skill.tags) == ['cooking']

    # ParseDict from dictionary
    skill_full = ParseDict(FULL_AGENT_SKILL, AgentSkill())
    assert skill_full.id == 'skill-123'
    assert list(skill_full.examples) == ['Find me a pasta recipe']
    assert list(skill_full.input_modes) == ['text/plain']


def test_agent_card():
    """Test AgentCard proto construction and ParseDict."""
    card = ParseDict(MINIMAL_AGENT_CARD, AgentCard())
    assert card.name == 'TestAgent'
    assert card.version == '1.0'
    assert len(card.skills) == 1
    assert card.skills[0].id == 'skill-123'
    assert not card.HasField('provider')  # Optional, not set


def test_security_scheme():
    """Test SecurityScheme oneof handling."""
    # API Key scheme
    api_key = APIKeySecurityScheme(
        name='X-API-KEY',
        location='header',  # location is a string in proto
    )
    scheme = SecurityScheme(api_key_security_scheme=api_key)
    assert scheme.HasField('api_key_security_scheme')
    assert scheme.api_key_security_scheme.name == 'X-API-KEY'
    assert scheme.api_key_security_scheme.location == 'header'


# --- Test Part Types ---


def test_text_part():
    """Test Part with text field (Part has text as a direct string field)."""
    # Part with text
    part = Part(text='Hello')
    assert part.text == 'Hello'
    # Check oneof
    assert part.WhichOneof('content') == 'text'


def test_part_with_url():
    """Test Part with url."""
    part = Part(
        url='file:///path/to/file.txt',
        media_type='text/plain',
    )
    assert part.url == 'file:///path/to/file.txt'
    assert part.media_type == 'text/plain'


def test_part_with_raw():
    """Test Part with raw bytes."""
    part = Part(
        raw=b'hello',
        filename='hello.txt',
    )
    assert part.raw == b'hello'
    assert part.filename == 'hello.txt'


def test_part_with_data():
    """Test Part with data."""
    s = Struct()
    s.update({'key': 'value'})
    part = Part(data=Value(struct_value=s))
    assert part.HasField('data')


# --- Test Message and Task ---


def test_message():
    """Test Message proto construction."""
    part = Part(text='Hello')

    msg = Message(
        role=Role.ROLE_USER,
        message_id='msg-123',
    )
    msg.parts.append(part)

    assert msg.role == Role.ROLE_USER
    assert msg.message_id == 'msg-123'
    assert len(msg.parts) == 1
    assert msg.parts[0].text == 'Hello'


def test_message_with_metadata():
    """Test Message with metadata."""
    msg = Message(
        role=Role.ROLE_AGENT,
        message_id='msg-456',
    )
    msg.metadata.update({'timestamp': 'now'})

    assert msg.role == Role.ROLE_AGENT
    assert dict(msg.metadata) == {'timestamp': 'now'}


def test_task_status():
    """Test TaskStatus proto construction."""
    status = TaskStatus(state=TaskState.TASK_STATE_SUBMITTED)
    assert status.state == TaskState.TASK_STATE_SUBMITTED
    assert not status.HasField('message')
    # timestamp is a Timestamp proto, default has seconds=0
    assert status.timestamp.seconds == 0

    # TaskStatus with timestamp
    from google.protobuf.timestamp_pb2 import Timestamp

    ts = Timestamp()
    ts.FromJsonString('2023-10-27T10:00:00Z')
    status_working = TaskStatus(
        state=TaskState.TASK_STATE_WORKING,
        timestamp=ts,
    )
    assert status_working.state == TaskState.TASK_STATE_WORKING
    assert status_working.timestamp.seconds == ts.seconds


def test_task():
    """Test Task proto construction."""
    status = TaskStatus(state=TaskState.TASK_STATE_SUBMITTED)
    task = Task(
        id='task-abc',
        context_id='session-xyz',
        status=status,
    )

    assert task.id == 'task-abc'
    assert task.context_id == 'session-xyz'
    assert task.status.state == TaskState.TASK_STATE_SUBMITTED
    assert len(task.history) == 0
    assert len(task.artifacts) == 0


def test_task_with_history():
    """Test Task with history."""
    status = TaskStatus(state=TaskState.TASK_STATE_WORKING)
    task = Task(
        id='task-abc',
        context_id='session-xyz',
        status=status,
    )

    # Add message to history
    msg = Message(role=Role.ROLE_USER, message_id='msg-1')
    msg.parts.append(Part(text='Hello'))
    task.history.append(msg)

    assert len(task.history) == 1
    assert task.history[0].role == Role.ROLE_USER


def test_task_with_artifacts():
    """Test Task with artifacts."""
    status = TaskStatus(state=TaskState.TASK_STATE_COMPLETED)
    task = Task(
        id='task-abc',
        context_id='session-xyz',
        status=status,
    )

    # Add artifact
    artifact = Artifact(artifact_id='artifact-123', name='result')
    s = Struct()
    s.update({'result': 42})
    v = Value(struct_value=s)
    artifact.parts.append(Part(data=v))
    task.artifacts.append(artifact)

    assert len(task.artifacts) == 1
    assert task.artifacts[0].artifact_id == 'artifact-123'
    assert task.artifacts[0].name == 'result'


# --- Test Request Types ---


def test_send_message_request():
    """Test SendMessageRequest proto construction."""
    msg = Message(role=Role.ROLE_USER, message_id='msg-123')
    msg.parts.append(Part(text='Hello'))

    request = SendMessageRequest(message=msg)
    assert request.message.role == Role.ROLE_USER
    assert request.message.parts[0].text == 'Hello'


def test_get_task_request():
    """Test GetTaskRequest proto construction."""
    request = GetTaskRequest(id='task-123')
    assert request.id == 'task-123'


def test_cancel_task_request():
    """Test CancelTaskRequest proto construction."""
    request = CancelTaskRequest(id='task-123')
    assert request.id == 'task-123'


def test_subscribe_to_task_request():
    """Test SubscribeToTaskRequest proto construction."""
    request = SubscribeToTaskRequest(id='task-123')
    assert request.id == 'task-123'


def test_set_task_push_notification_config_request():
    """Test CreateTaskPushNotificationConfigRequest proto construction."""
    config = PushNotificationConfig(
        url='https://example.com/webhook',
    )
    request = CreateTaskPushNotificationConfigRequest(
        task_id='task-123',
        config=config,
    )
    assert request.task_id == 'task-123'
    assert request.config.url == 'https://example.com/webhook'


def test_get_task_push_notification_config_request():
    """Test GetTaskPushNotificationConfigRequest proto construction."""
    request = GetTaskPushNotificationConfigRequest(
        task_id='task-123', id='config-1'
    )
    assert request.task_id == 'task-123'


# --- Test Enum Values ---


def test_role_enum():
    """Test Role enum values."""
    assert Role.ROLE_UNSPECIFIED == 0
    assert Role.ROLE_USER == 1
    assert Role.ROLE_AGENT == 2


def test_task_state_enum():
    """Test TaskState enum values."""
    assert TaskState.TASK_STATE_UNSPECIFIED == 0
    assert TaskState.TASK_STATE_SUBMITTED == 1
    assert TaskState.TASK_STATE_WORKING == 2
    assert TaskState.TASK_STATE_COMPLETED == 3
    assert TaskState.TASK_STATE_FAILED == 4
    assert TaskState.TASK_STATE_CANCELED == 5
    assert TaskState.TASK_STATE_INPUT_REQUIRED == 6
    assert TaskState.TASK_STATE_REJECTED == 7
    assert TaskState.TASK_STATE_AUTH_REQUIRED == 8


# --- Test ParseDict and MessageToDict ---


def test_parse_dict_agent_card():
    """Test ParseDict for AgentCard."""
    card = ParseDict(MINIMAL_AGENT_CARD, AgentCard())
    assert card.name == 'TestAgent'
    assert card.supported_interfaces[0].url == 'http://example.com/agent'

    # Round-trip through MessageToDict
    card_dict = MessageToDict(card)
    assert card_dict['name'] == 'TestAgent'
    assert (
        card_dict['supportedInterfaces'][0]['url'] == 'http://example.com/agent'
    )


def test_parse_dict_task():
    """Test ParseDict for Task with nested structures."""
    task_data = {
        'id': 'task-123',
        'contextId': 'ctx-456',
        'status': {
            'state': 'TASK_STATE_WORKING',
        },
        'history': [
            {
                'role': 'ROLE_USER',
                'messageId': 'msg-1',
                'parts': [{'text': 'Hello'}],
            }
        ],
    }
    task = ParseDict(task_data, Task())
    assert task.id == 'task-123'
    assert task.context_id == 'ctx-456'
    assert task.status.state == TaskState.TASK_STATE_WORKING
    assert len(task.history) == 1
    assert task.history[0].role == Role.ROLE_USER


def test_message_to_dict_preserves_structure():
    """Test that MessageToDict produces correct structure."""
    msg = Message(role=Role.ROLE_USER, message_id='msg-123')
    msg.parts.append(Part(text='Hello'))

    msg_dict = MessageToDict(msg)
    assert msg_dict['role'] == 'ROLE_USER'
    assert msg_dict['messageId'] == 'msg-123'
    # Part.text is a direct string field in proto
    assert msg_dict['parts'][0]['text'] == 'Hello'


# --- Test Proto Copy and Equality ---


def test_proto_copy():
    """Test copying proto messages."""
    original = Task(
        id='task-123',
        context_id='ctx-456',
        status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
    )

    # Copy using CopyFrom
    copy = Task()
    copy.CopyFrom(original)

    assert copy.id == 'task-123'
    assert copy.context_id == 'ctx-456'
    assert copy.status.state == TaskState.TASK_STATE_SUBMITTED

    # Modifying copy doesn't affect original
    copy.id = 'task-999'
    assert original.id == 'task-123'


def test_proto_equality():
    """Test proto message equality."""
    task1 = Task(
        id='task-123',
        context_id='ctx-456',
        status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
    )
    task2 = Task(
        id='task-123',
        context_id='ctx-456',
        status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
    )

    assert task1 == task2

    task2.id = 'task-999'
    assert task1 != task2


# --- Test HasField for Optional Fields ---


def test_has_field_optional():
    """Test HasField for checking optional field presence."""
    status = TaskStatus(state=TaskState.TASK_STATE_SUBMITTED)
    assert not status.HasField('message')

    # Add message
    msg = Message(role=Role.ROLE_USER, message_id='msg-1')
    status.message.CopyFrom(msg)
    assert status.HasField('message')


def test_has_field_oneof():
    """Test HasField for oneof fields."""
    part = Part(text='Hello')
    assert part.HasField('text')
    assert not part.HasField('url')
    assert not part.HasField('data')

    # WhichOneof for checking which oneof is set
    assert part.WhichOneof('content') == 'text'


# --- Test Repeated Fields ---


def test_repeated_field_operations():
    """Test operations on repeated fields."""
    task = Task(
        id='task-123',
        context_id='ctx-456',
        status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
    )

    # append
    msg1 = Message(role=Role.ROLE_USER, message_id='msg-1')
    task.history.append(msg1)
    assert len(task.history) == 1

    # extend
    msg2 = Message(role=Role.ROLE_AGENT, message_id='msg-2')
    msg3 = Message(role=Role.ROLE_USER, message_id='msg-3')
    task.history.extend([msg2, msg3])
    assert len(task.history) == 3

    # iteration
    roles = [m.role for m in task.history]
    assert roles == [Role.ROLE_USER, Role.ROLE_AGENT, Role.ROLE_USER]


def test_map_field_operations():
    """Test operations on map fields."""
    msg = Message(role=Role.ROLE_USER, message_id='msg-1')

    # Update map
    msg.metadata.update({'key1': 'value1', 'key2': 'value2'})
    assert dict(msg.metadata) == {'key1': 'value1', 'key2': 'value2'}

    # Access individual keys
    assert msg.metadata['key1'] == 'value1'

    # Check containment
    assert 'key1' in msg.metadata
    assert 'key3' not in msg.metadata


# --- Test Serialization ---


def test_serialize_to_bytes():
    """Test serializing proto to bytes."""
    msg = Message(role=Role.ROLE_USER, message_id='msg-123')
    msg.parts.append(Part(text='Hello'))

    # Serialize
    data = msg.SerializeToString()
    assert isinstance(data, bytes)
    assert len(data) > 0

    # Deserialize
    msg2 = Message()
    msg2.ParseFromString(data)
    assert msg2.role == Role.ROLE_USER
    assert msg2.message_id == 'msg-123'
    assert msg2.parts[0].text == 'Hello'


def test_serialize_to_json():
    """Test serializing proto to JSON via MessageToDict."""
    msg = Message(role=Role.ROLE_USER, message_id='msg-123')
    msg.parts.append(Part(text='Hello'))

    # MessageToDict for JSON-serializable dict
    msg_dict = MessageToDict(msg)

    import json

    json_str = json.dumps(msg_dict)
    assert 'ROLE_USER' in json_str
    assert 'msg-123' in json_str


# --- Test Default Values ---


def test_default_values():
    """Test proto default values."""
    # Empty message has defaults
    msg = Message()
    assert msg.role == Role.ROLE_UNSPECIFIED  # Enum default is 0
    assert msg.message_id == ''  # String default is empty
    assert len(msg.parts) == 0  # Repeated field default is empty

    # Task status defaults
    status = TaskStatus()
    assert status.state == TaskState.TASK_STATE_UNSPECIFIED
    assert status.timestamp.seconds == 0  # Timestamp proto default


def test_clear_field():
    """Test clearing fields."""
    msg = Message(role=Role.ROLE_USER, message_id='msg-123')
    assert msg.message_id == 'msg-123'

    msg.ClearField('message_id')
    assert msg.message_id == ''  # Back to default

    # Clear nested message
    status = TaskStatus(state=TaskState.TASK_STATE_WORKING)
    status.message.CopyFrom(Message(role=Role.ROLE_USER))
    assert status.HasField('message')

    status.ClearField('message')
    assert not status.HasField('message')
