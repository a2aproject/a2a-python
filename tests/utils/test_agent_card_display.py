"""Tests for display_agent_card utility."""

import pytest

from a2a.types.a2a_pb2 import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentProvider,
    AgentSkill,
)
from a2a.helpers.agent_card import display_agent_card


@pytest.fixture
def full_agent_card() -> AgentCard:
    return AgentCard(
        name='Sample Agent',
        description='A sample agent.',
        version='1.0.0',
        documentation_url='https://docs.example.com',
        icon_url='https://example.com/icon.png',
        provider=AgentProvider(
            organization='Example Org', url='https://example.com'
        ),
        supported_interfaces=[
            AgentInterface(
                url='http://localhost:9999/a2a/jsonrpc',
                protocol_binding='JSONRPC',
                protocol_version='1.0',
            ),
            AgentInterface(
                url='http://localhost:9999/a2a/rest',
                protocol_binding='HTTP+JSON',
                protocol_version='1.0',
                tenant='tenant-a',
            ),
        ],
        capabilities=AgentCapabilities(
            streaming=True,
            push_notifications=False,
            extended_agent_card=True,
        ),
        default_input_modes=['text'],
        default_output_modes=['text', 'task-status'],
        skills=[
            AgentSkill(
                id='skill-1',
                name='My Skill',
                description='Does something useful.',
                tags=['foo', 'bar'],
                examples=['Do the thing', 'Another example'],
            ),
            AgentSkill(
                id='skill-2',
                name='Other Skill',
                description='Does something else.',
                tags=['baz'],
            ),
        ],
    )


class TestDisplayAgentCard:
    def test_full_card_output(
        self, full_agent_card: AgentCard, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Golden test: exact output for a fully-populated card."""
        display_agent_card(full_agent_card)
        assert capsys.readouterr().out == (
            '====================================================\n'
            '                     AgentCard                      \n'
            '====================================================\n'
            '--- General ---\n'
            'Name        : Sample Agent\n'
            'Description : A sample agent.\n'
            'Version     : 1.0.0\n'
            'Docs URL    : https://docs.example.com\n'
            'Icon URL    : https://example.com/icon.png\n'
            'Provider    : Example Org (https://example.com)\n'
            '\n'
            '--- Interfaces ---\n'
            '  [0] http://localhost:9999/a2a/jsonrpc  (JSONRPC 1.0)\n'
            '  [1] http://localhost:9999/a2a/rest  (HTTP+JSON 1.0, tenant=tenant-a)\n'
            '\n'
            '--- Capabilities ---\n'
            'Streaming           : True\n'
            'Push notifications  : False\n'
            'Extended agent card : True\n'
            '\n'
            '--- I/O Modes ---\n'
            'Input  : text\n'
            'Output : text, task-status\n'
            '\n'
            '--- Skills ---\n'
            '----------------------------------------------------\n'
            '  ID          : skill-1\n'
            '  Name        : My Skill\n'
            '  Description : Does something useful.\n'
            '  Tags        : foo, bar\n'
            '  Example     : Do the thing\n'
            '  Example     : Another example\n'
            '----------------------------------------------------\n'
            '  ID          : skill-2\n'
            '  Name        : Other Skill\n'
            '  Description : Does something else.\n'
            '  Tags        : baz\n'
            '====================================================\n'
        )

    def test_empty_card_output(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Golden test: exact output for a card with only default/empty fields.

        An empty supported_interfaces section signals a malformed card —
        the bare header with no entries is intentional and visible to the user.
        """
        display_agent_card(AgentCard())
        assert capsys.readouterr().out == (
            '====================================================\n'
            '                     AgentCard                      \n'
            '====================================================\n'
            '--- General ---\n'
            'Name        : \n'
            'Description : \n'
            'Version     : \n'
            '\n'
            '--- Interfaces ---\n'
            '\n'
            '--- Capabilities ---\n'
            'Streaming           : False\n'
            'Push notifications  : False\n'
            'Extended agent card : False\n'
            '\n'
            '--- I/O Modes ---\n'
            'Input  : (none)\n'
            'Output : (none)\n'
            '\n'
            '--- Skills ---\n'
            '  (none)\n'
            '====================================================\n'
        )

    def test_interface_without_protocol_version_has_no_trailing_space(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """No trailing space in the binding field when protocol_version is not set."""
        card = AgentCard(
            supported_interfaces=[
                AgentInterface(
                    url='127.0.0.1:50051',
                    protocol_binding='GRPC',
                )
            ]
        )
        display_agent_card(card)
        assert '  [0] 127.0.0.1:50051  (GRPC)' in capsys.readouterr().out

    def test_interface_without_binding_or_version_has_no_parentheses(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """No parentheses when neither protocol_binding nor protocol_version are set."""
        card = AgentCard(
            supported_interfaces=[AgentInterface(url='127.0.0.1:50051')]
        )
        display_agent_card(card)
        assert '  [0] 127.0.0.1:50051\n' in capsys.readouterr().out

    def test_provider_with_url(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Provider shows organization and URL in parentheses when both are set."""
        card = AgentCard(
            provider=AgentProvider(
                organization='Example Org',
                url='https://example.com',
            ),
        )
        display_agent_card(card)
        assert (
            'Provider    : Example Org (https://example.com)'
            in capsys.readouterr().out
        )

    def test_provider_without_url_has_no_empty_parentheses(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """No empty parentheses when provider URL is not set."""
        card = AgentCard(provider=AgentProvider(organization='Example Org'))
        display_agent_card(card)
        out = capsys.readouterr().out
        assert 'Provider    : Example Org' in out
        assert '()' not in out
