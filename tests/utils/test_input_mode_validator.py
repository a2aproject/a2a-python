"""Tests for media-type validation against an agent card's declared input modes."""

import pytest

from a2a.types.a2a_pb2 import AgentCard, Message, Part, Role
from a2a.utils.errors import ContentTypeNotSupportedError
from a2a.utils.input_mode_validator import validate_input_modes


def make_card(*input_modes: str) -> AgentCard:
    return AgentCard(
        name='test_agent', version='1.0', default_input_modes=list(input_modes)
    )


def make_message(*parts: Part) -> Message:
    return Message(role=Role.ROLE_USER, message_id='msg-1', parts=list(parts))


def test_declared_media_type_is_accepted():
    validate_input_modes(
        make_message(Part(text='hi', media_type='text/plain')),
        make_card('text/plain'),
    )


def test_undeclared_media_type_is_rejected():
    with pytest.raises(ContentTypeNotSupportedError) as exc_info:
        validate_input_modes(
            make_message(Part(text='hi', media_type='application/x-nope')),
            make_card('text/plain'),
        )

    assert 'application/x-nope' in exc_info.value.message


def test_absent_media_type_is_not_checked():
    """A proto3 string defaults to '', which states nothing to contradict."""
    validate_input_modes(make_message(Part(text='hi')), make_card('image/png'))


def test_card_declaring_no_input_modes_accepts_anything():
    validate_input_modes(
        make_message(Part(text='hi', media_type='application/x-nope')),
        make_card(),
    )


def test_every_part_is_checked_not_just_the_first():
    with pytest.raises(ContentTypeNotSupportedError):
        validate_input_modes(
            make_message(
                Part(text='ok', media_type='text/plain'),
                Part(text='bad', media_type='application/x-nope'),
            ),
            make_card('text/plain'),
        )


def test_any_declared_mode_may_match():
    validate_input_modes(
        make_message(Part(text='hi', media_type='image/png')),
        make_card('text/plain', 'image/png'),
    )
