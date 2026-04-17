"""Tests for proto helpers."""

import pytest
from a2a.helpers.proto_helpers import (
    new_message,
    new_text_message,
    get_message_text,
    new_artifact,
    new_text_artifact,
    get_artifact_text,
    new_task_from_user_message,
    new_task,
    get_text_parts,
    new_text_status_update_event,
    new_text_artifact_update_event,
    get_stream_response_text,
)
from a2a.types.a2a_pb2 import (
    Part,
    Role,
    Message,
    Artifact,
    Task,
    TaskState,
    StreamResponse,
)

# --- Message Helpers Tests ---


def test_new_message() -> None:
    parts = [Part(text='hello')]
    msg = new_message(
        parts=parts, role=Role.ROLE_USER, context_id='ctx1', task_id='task1'
    )
    assert msg.role == Role.ROLE_USER
    assert msg.parts == parts
    assert msg.context_id == 'ctx1'
    assert msg.task_id == 'task1'
    assert msg.message_id != ''


def test_new_text_message() -> None:
    msg = new_text_message(
        text='hello', context_id='ctx1', task_id='task1', role=Role.ROLE_USER
    )
    assert msg.role == Role.ROLE_USER
    assert len(msg.parts) == 1
    assert msg.parts[0].text == 'hello'
    assert msg.context_id == 'ctx1'
    assert msg.task_id == 'task1'
    assert msg.message_id != ''


def test_get_message_text() -> None:
    msg = Message(parts=[Part(text='hello'), Part(text='world')])
    assert get_message_text(msg) == 'hello\nworld'
    assert get_message_text(msg, delimiter=' ') == 'hello world'


# --- Artifact Helpers Tests ---


def test_new_artifact() -> None:
    parts = [Part(text='content')]
    art = new_artifact(parts=parts, name='test', description='desc')
    assert art.name == 'test'
    assert art.description == 'desc'
    assert art.parts == parts
    assert art.artifact_id != ''


def test_new_text_artifact() -> None:
    art = new_text_artifact(name='test', text='content', description='desc')
    assert art.name == 'test'
    assert art.description == 'desc'
    assert len(art.parts) == 1
    assert art.parts[0].text == 'content'
    assert art.artifact_id != ''


def test_new_text_artifact_with_id() -> None:
    art = new_text_artifact(
        name='test', text='content', description='desc', artifact_id='art1'
    )
    assert art.name == 'test'
    assert art.description == 'desc'
    assert len(art.parts) == 1
    assert art.parts[0].text == 'content'
    assert art.artifact_id == 'art1'


def test_get_artifact_text() -> None:
    art = Artifact(parts=[Part(text='hello'), Part(text='world')])
    assert get_artifact_text(art) == 'hello\nworld'
    assert get_artifact_text(art, delimiter=' ') == 'hello world'


# --- Task Helpers Tests ---


def test_new_task_from_user_message() -> None:
    msg = Message(
        role=Role.ROLE_USER,
        parts=[Part(text='hello')],
        task_id='task1',
        context_id='ctx1',
    )
    task = new_task_from_user_message(msg)
    assert task.id == 'task1'
    assert task.context_id == 'ctx1'
    assert task.status.state == TaskState.TASK_STATE_SUBMITTED
    assert len(task.history) == 1
    assert task.history[0] == msg


def test_new_task_from_user_message_empty_parts() -> None:
    msg = Message(role=Role.ROLE_USER, parts=[])
    with pytest.raises(ValueError, match='Message parts cannot be empty'):
        new_task_from_user_message(msg)


def test_new_task_from_user_message_empty_text() -> None:
    msg = Message(role=Role.ROLE_USER, parts=[Part(text='')])
    with pytest.raises(ValueError, match='Message.text cannot be empty'):
        new_task_from_user_message(msg)


def test_new_task() -> None:
    task = new_task(
        task_id='task1', context_id='ctx1', state=TaskState.TASK_STATE_WORKING
    )
    assert task.id == 'task1'
    assert task.context_id == 'ctx1'
    assert task.status.state == TaskState.TASK_STATE_WORKING
    assert len(task.history) == 0
    assert len(task.artifacts) == 0


# --- Part Helpers Tests ---


def test_get_text_parts() -> None:
    parts = [
        Part(text='hello'),
        Part(url='http://example.com'),
        Part(text='world'),
    ]
    assert get_text_parts(parts) == ['hello', 'world']


# --- Event & Stream Helpers Tests ---


def test_new_text_status_update_event() -> None:
    event = new_text_status_update_event(
        task_id='task1',
        context_id='ctx1',
        state=TaskState.TASK_STATE_WORKING,
        text='progress',
    )
    assert event.task_id == 'task1'
    assert event.context_id == 'ctx1'
    assert event.status.state == TaskState.TASK_STATE_WORKING
    assert event.status.message.parts[0].text == 'progress'


def test_new_text_artifact_update_event() -> None:
    event = new_text_artifact_update_event(
        task_id='task1',
        context_id='ctx1',
        name='test',
        text='content',
        append=True,
        last_chunk=True,
    )
    assert event.task_id == 'task1'
    assert event.context_id == 'ctx1'
    assert event.artifact.name == 'test'
    assert event.artifact.parts[0].text == 'content'
    assert event.append is True
    assert event.last_chunk is True


def test_new_text_artifact_update_event_with_id() -> None:
    event = new_text_artifact_update_event(
        task_id='task1',
        context_id='ctx1',
        name='test',
        text='content',
        artifact_id='art1',
    )
    assert event.task_id == 'task1'
    assert event.context_id == 'ctx1'
    assert event.artifact.name == 'test'
    assert event.artifact.parts[0].text == 'content'
    assert event.artifact.artifact_id == 'art1'


def test_get_stream_response_text_message() -> None:
    resp = StreamResponse(message=Message(parts=[Part(text='hello')]))
    assert get_stream_response_text(resp) == 'hello'


def test_get_stream_response_text_task() -> None:
    resp = StreamResponse(
        task=Task(artifacts=[Artifact(parts=[Part(text='hello')])])
    )
    assert get_stream_response_text(resp) == 'hello'


def test_get_stream_response_text_status_update() -> None:
    resp = StreamResponse(
        status_update=new_text_status_update_event(
            't', 'c', TaskState.TASK_STATE_WORKING, 'hello'
        )
    )
    assert get_stream_response_text(resp) == 'hello'


def test_get_stream_response_text_artifact_update() -> None:
    resp = StreamResponse(
        artifact_update=new_text_artifact_update_event('t', 'c', 'n', 'hello')
    )
    assert get_stream_response_text(resp) == 'hello'


def test_get_stream_response_text_empty() -> None:
    resp = StreamResponse()
    assert get_stream_response_text(resp) == ''
