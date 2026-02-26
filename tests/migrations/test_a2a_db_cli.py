import os
import argparse
from unittest.mock import MagicMock, patch
import pytest
from a2a.a2a_db_cli import run_migrations


@pytest.fixture
def mock_alembic_command():
    with (
        patch('alembic.command.upgrade') as mock_upgrade,
        patch('alembic.command.downgrade') as mock_downgrade,
    ):
        yield mock_upgrade, mock_downgrade


@pytest.fixture
def mock_alembic_config():
    with patch('a2a.a2a_db_cli.Config') as mock_config:
        yield mock_config


def test_cli_upgrade_offline(mock_alembic_command, mock_alembic_config):
    mock_upgrade, _ = mock_alembic_command
    custom_owner = 'test-owner'
    tasks_table = 'my_tasks'
    push_table = 'my_push'

    # Simulate: a2a-db upgrade head --sql --owner-name test-owner --tasks-table my_tasks --push-notification-table my_push -v
    test_args = [
        'a2a-db',
        'upgrade',
        'head',
        '--sql',
        '--owner-name',
        custom_owner,
        '--tasks-table',
        tasks_table,
        '--push-notification-table',
        push_table,
        '-v',
    ]
    with patch('sys.argv', test_args):
        with patch.dict(os.environ, {'DATABASE_URL': 'sqlite:///test.db'}):
            run_migrations()

    # Verify upgrade parameters
    args, kwargs = mock_upgrade.call_args
    assert kwargs['sql'] is True
    assert args[1] == 'head'

    # Verify options were set in config instance
    # Note: Using assert_any_call because multiple options are set
    mock_alembic_config.return_value.set_main_option.assert_any_call(
        'owner', custom_owner
    )
    mock_alembic_config.return_value.set_main_option.assert_any_call(
        'tasks_tables', tasks_table
    )
    mock_alembic_config.return_value.set_main_option.assert_any_call(
        'push_notification_tables', push_table
    )
    mock_alembic_config.return_value.set_main_option.assert_any_call(
        'verbose', 'true'
    )


def test_cli_downgrade_offline(mock_alembic_command, mock_alembic_config):
    _, mock_downgrade = mock_alembic_command
    tasks_table = 'old_tasks'

    # Simulate: a2a-db downgrade base --sql --tasks-table old_tasks
    test_args = [
        'a2a-db',
        'downgrade',
        'base',
        '--sql',
        '--tasks-table',
        tasks_table,
    ]
    with patch('sys.argv', test_args):
        with patch.dict(os.environ, {'DATABASE_URL': 'sqlite:///test.db'}):
            run_migrations()

    args, kwargs = mock_downgrade.call_args
    assert kwargs['sql'] is True
    assert args[1] == 'base'

    # Verify tables option
    mock_alembic_config.return_value.set_main_option.assert_any_call(
        'tasks_tables', tasks_table
    )


def test_cli_default_upgrade(mock_alembic_command, mock_alembic_config):
    mock_upgrade, _ = mock_alembic_command

    # Simulate: a2a-db (no args)
    test_args = ['a2a-db']
    with patch('sys.argv', test_args):
        with patch.dict(os.environ, {'DATABASE_URL': 'sqlite:///test.db'}):
            run_migrations()

    # Should default to upgrade head
    mock_upgrade.assert_called_once()
    args, kwargs = mock_upgrade.call_args
    assert args[1] == 'head'
    assert kwargs['sql'] is False


def test_cli_database_url_flag(mock_alembic_command, mock_alembic_config):
    mock_upgrade, _ = mock_alembic_command
    custom_db = 'sqlite:///custom_cli.db'

    # Simulate: a2a-db --database-url sqlite:///custom_cli.db
    test_args = ['a2a-db', '--database-url', custom_db]
    with patch('sys.argv', test_args):
        with patch.dict(os.environ, {}, clear=True):
            run_migrations()
            # Verify the CLI tool set the environment variable
            assert os.environ['DATABASE_URL'] == custom_db

    mock_upgrade.assert_called()


def test_cli_owner_with_downgrade_error(
    mock_alembic_command, mock_alembic_config
):
    # This should trigger parser.error()
    test_args = ['a2a-db', 'downgrade', 'base', '--owner-name', 'some-owner']

    with patch('sys.argv', test_args):
        with patch.dict(os.environ, {'DATABASE_URL': 'sqlite:///test.db'}):
            # argparse calls sys.exit on error
            with pytest.raises(SystemExit):
                run_migrations()
