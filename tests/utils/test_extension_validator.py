import pytest

from a2a.types.a2a_pb2 import AgentCapabilities, AgentCard, AgentExtension
from a2a.utils.errors import ExtensionSupportRequiredError
from a2a.utils.extension_validator import validate_required_extensions


REQUIRED = 'https://example.com/ext/required/v1'
OPTIONAL = 'https://example.com/ext/optional/v1'


def _card(*extensions: AgentExtension) -> AgentCard:
    return AgentCard(capabilities=AgentCapabilities(extensions=extensions))


def test_rejects_missing_required_extension():
    card = _card(AgentExtension(uri=REQUIRED, required=True))

    with pytest.raises(ExtensionSupportRequiredError) as excinfo:
        validate_required_extensions(set(), card)

    assert REQUIRED in excinfo.value.message


def test_accepts_requested_required_extension():
    card = _card(AgentExtension(uri=REQUIRED, required=True))

    validate_required_extensions({REQUIRED}, card)


def test_optional_extension_is_not_enforced():
    card = _card(AgentExtension(uri=OPTIONAL))

    validate_required_extensions(set(), card)


def test_names_every_missing_required_extension():
    other = 'https://example.com/ext/other/v1'
    card = _card(
        AgentExtension(uri=REQUIRED, required=True),
        AgentExtension(uri=other, required=True),
    )

    with pytest.raises(ExtensionSupportRequiredError) as excinfo:
        validate_required_extensions({OPTIONAL}, card)

    assert REQUIRED in excinfo.value.message
    assert other in excinfo.value.message


def test_card_without_extensions_accepts_anything():
    validate_required_extensions({OPTIONAL}, AgentCard())
