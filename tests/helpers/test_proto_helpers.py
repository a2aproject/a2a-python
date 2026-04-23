"""Tests for proto helpers."""

import pytest

from a2a.helpers.proto_helpers import (
    get_artifact_text,
    get_message_text,
    get_stream_response_text,
    get_text_parts,
    new_artifact,
    new_data_artifact,
    new_data_message,
    new_data_part,
    new_message,
    new_raw_artifact,
    new_raw_message,
    new_raw_part,
    new_task,
    new_task_from_user_message,
    new_text_artifact,
    new_text_artifact_update_event,
    new_text_message,
    new_text_part,
    new_text_status_update_event,
    new_url_artifact,
    new_url_message,
    new_url_part,
)
from a2a.types.a2a_pb2 import (
    Artifact,
    Message,
    Part,
    Role,
    StreamResponse,
    Task,
    TaskState,
)


# --- Message Helpers Tests ---


def test_new_message() -> None:
    parts = [Part(text='hello')]
    msg = new_message(
        parts, context_id='ctx1', task_id='task1', role=Role.ROLE_USER
    )
    assert msg.role == Role.ROLE_USER
    assert msg.parts == parts
    assert msg.context_id == 'ctx1'
    assert msg.task_id == 'task1'
    assert msg.message_id != ''


def test_new_text_message() -> None:
    msg = new_text_message(
        'hello',
        media_type='text/plain',
        context_id='ctx1',
        task_id='task1',
        role=Role.ROLE_USER,
    )
    assert msg.role == Role.ROLE_USER
    assert len(msg.parts) == 1
    assert msg.parts[0].text == 'hello'
    assert msg.parts[0].media_type == 'text/plain'
    assert msg.context_id == 'ctx1'
    assert msg.task_id == 'task1'
    assert msg.message_id != ''


def test_new_data_message() -> None:
    msg = new_data_message(
        data={'key': 'value'},
        media_type='application/json',
        context_id='ctx1',
        task_id='task1',
        role=Role.ROLE_USER,
    )
    assert msg.role == Role.ROLE_USER
    assert len(msg.parts) == 1
    assert msg.parts[0].HasField('data')
    assert msg.parts[0].data.struct_value.fields['key'].string_value == 'value'
    assert msg.parts[0].media_type == 'application/json'
    assert msg.context_id == 'ctx1'
    assert msg.task_id == 'task1'
    assert msg.message_id != ''


def test_new_raw_message() -> None:
    msg = new_raw_message(
        b'\x89PNG',
        media_type='image/png',
        filename='img.png',
        context_id='ctx1',
        task_id='task1',
        role=Role.ROLE_USER,
    )
    assert msg.role == Role.ROLE_USER
    assert len(msg.parts) == 1
    assert msg.parts[0].HasField('raw')
    assert msg.parts[0].raw == b'\x89PNG'
    assert msg.parts[0].media_type == 'image/png'
    assert msg.parts[0].filename == 'img.png'
    assert msg.context_id == 'ctx1'
    assert msg.task_id == 'task1'
    assert msg.message_id != ''


def test_new_url_message() -> None:
    msg = new_url_message(
        'https://example.com/file.pdf',
        media_type='application/pdf',
        filename='file.pdf',
        context_id='ctx1',
        task_id='task1',
        role=Role.ROLE_USER,
    )
    assert msg.role == Role.ROLE_USER
    assert len(msg.parts) == 1
    assert msg.parts[0].HasField('url')
    assert msg.parts[0].url == 'https://example.com/file.pdf'
    assert msg.parts[0].media_type == 'application/pdf'
    assert msg.parts[0].filename == 'file.pdf'
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


def test_new_data_artifact() -> None:
    art = new_data_artifact(
        name='result', data={'score': 1.0}, description='desc'
    )
    assert art.name == 'result'
    assert art.description == 'desc'
    assert len(art.parts) == 1
    assert art.parts[0].HasField('data')
    assert art.parts[0].data.struct_value.fields['score'].number_value == 1.0
    assert art.artifact_id != ''


def test_new_data_artifact_with_id() -> None:
    art = new_data_artifact(name='result', data={'x': 'y'}, artifact_id='art1')
    assert art.artifact_id == 'art1'
    assert art.parts[0].data.struct_value.fields['x'].string_value == 'y'


def test_new_raw_artifact() -> None:
    art = new_raw_artifact(
        name='screenshot',
        raw=b'\x89PNG',
        media_type='image/png',
        filename='screen.png',
        description='desc',
        artifact_id='art1',
    )
    assert art.name == 'screenshot'
    assert art.description == 'desc'
    assert art.artifact_id == 'art1'
    assert len(art.parts) == 1
    assert art.parts[0].HasField('raw')
    assert art.parts[0].raw == b'\x89PNG'
    assert art.parts[0].media_type == 'image/png'
    assert art.parts[0].filename == 'screen.png'


def test_new_raw_artifact_minimal() -> None:
    art = new_raw_artifact(name='file', raw=b'data')
    assert art.parts[0].raw == b'data'
    assert art.artifact_id != ''


def test_new_url_artifact() -> None:
    art = new_url_artifact(
        name='report',
        url='https://example.com/report.pdf',
        media_type='application/pdf',
        filename='report.pdf',
        description='desc',
        artifact_id='art1',
    )
    assert art.name == 'report'
    assert art.description == 'desc'
    assert art.artifact_id == 'art1'
    assert len(art.parts) == 1
    assert art.parts[0].HasField('url')
    assert art.parts[0].url == 'https://example.com/report.pdf'
    assert art.parts[0].media_type == 'application/pdf'
    assert art.parts[0].filename == 'report.pdf'


def test_new_url_artifact_minimal() -> None:
    art = new_url_artifact(name='img', url='https://example.com/img.png')
    assert art.parts[0].url == 'https://example.com/img.png'
    assert art.artifact_id != ''


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


def test_new_text_part() -> None:
    part = new_text_part('hello')
    assert part.HasField('text')
    assert part.text == 'hello'
    assert part.media_type == ''


def test_new_text_part_with_media_type() -> None:
    part = new_text_part('# Hello', media_type='text/markdown')
    assert part.HasField('text')
    assert part.text == '# Hello'
    assert part.media_type == 'text/markdown'


def test_new_data_part_from_dict() -> None:
    part = new_data_part({'key': 'value', 'count': 42})
    assert part.HasField('data')
    assert part.data.struct_value.fields['key'].string_value == 'value'
    assert part.data.struct_value.fields['count'].number_value == 42
    assert part.media_type == ''


def test_new_data_part_with_media_type() -> None:
    part = new_data_part({'key': 'value'}, media_type='application/json')
    assert part.HasField('data')
    assert part.media_type == 'application/json'


def test_new_data_part_from_list() -> None:
    part = new_data_part([1, 2, 3])
    assert part.HasField('data')
    assert part.data.list_value.values[0].number_value == 1
    assert part.data.list_value.values[1].number_value == 2
    assert part.data.list_value.values[2].number_value == 3


def test_new_raw_part() -> None:
    part = new_raw_part(b'\x89PNG', media_type='image/png', filename='img.png')
    assert part.HasField('raw')
    assert part.raw == b'\x89PNG'
    assert part.media_type == 'image/png'
    assert part.filename == 'img.png'


def test_new_raw_part_minimal() -> None:
    part = new_raw_part(b'data')
    assert part.HasField('raw')
    assert part.raw == b'data'
    assert part.media_type == ''
    assert part.filename == ''


def test_new_url_part() -> None:
    part = new_url_part(
        'https://example.com/file.pdf',
        media_type='application/pdf',
        filename='file.pdf',
    )
    assert part.HasField('url')
    assert part.url == 'https://example.com/file.pdf'
    assert part.media_type == 'application/pdf'
    assert part.filename == 'file.pdf'


def test_new_url_part_minimal() -> None:
    part = new_url_part('https://example.com/img.png')
    assert part.HasField('url')
    assert part.url == 'https://example.com/img.png'
    assert part.media_type == ''
    assert part.filename == ''


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
