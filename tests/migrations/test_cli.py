import os
import argparse
from unittest.mock import MagicMock, patch
import pytest
from a2a.cli import run_migrations


@pytest.fixture
def mock_alembic_command():
    with (
        patch('alembic.command.upgrade') as mock_upgrade,
        patch('alembic.command.downgrade') as mock_downgrade,
    ):
        yield mock_upgrade, mock_downgrade


@pytest.fixture
def mock_alembic_config():
    with patch('a2a.cli.Config') as mock_config:
        yield mock_config


def test_cli_upgrade_offline(mock_alembic_command, mock_alembic_config):
    mock_upgrade, _ = mock_alembic_command
    custom_owner = 'test-owner'
    target_tables = ['table1', 'table2']

    # Simulate: a2a-db upgrade head --sql -o test-owner -t table1 -t table2 -v
    test_args = [
        'a2a-db',
        'upgrade',
        'head',
        '--sql',
        '-o',
        custom_owner,
        '-t',
        target_tables[0],
        '-t',
        target_tables[1],
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
    mock_alembic_config.return_value.set_main_option.assert_any_call(
        'owner', custom_owner
    )
    mock_alembic_config.return_value.set_main_option.assert_any_call(
        'tables', ','.join(target_tables)
    )
    mock_alembic_config.return_value.set_main_option.assert_any_call(
        'verbose', 'true'
    )


def test_cli_downgrade_offline(mock_alembic_command, mock_alembic_config):
    _, mock_downgrade = mock_alembic_command
    target_table = 'only_tasks'

    # Simulate: a2a-db downgrade base --sql -t only_tasks -v
    test_args = [
        'a2a-db',
        'downgrade',
        'base',
        '--sql',
        '-t',
        target_table,
        '-v',
    ]
    with patch('sys.argv', test_args):
        with patch.dict(os.environ, {'DATABASE_URL': 'sqlite:///test.db'}):
            run_migrations()

    args, kwargs = mock_downgrade.call_args
    assert kwargs['sql'] is True
    assert args[1] == 'base'

    # Verify options
    mock_alembic_config.return_value.set_main_option.assert_any_call(
        'tables', target_table
    )
    mock_alembic_config.return_value.set_main_option.assert_any_call(
        'verbose', 'true'
    )


def test_cli_global_offline(mock_alembic_command, mock_alembic_config):
    mock_upgrade, _ = mock_alembic_command

    # Simulate: a2a-db --sql -v (defaults to upgrade head)
    test_args = ['a2a-db', '--sql', '-v']
    with patch('sys.argv', test_args):
        with patch.dict(os.environ, {'DATABASE_URL': 'sqlite:///test.db'}):
            run_migrations()

    # Verify upgrade was called with sql=True
    args, kwargs = mock_upgrade.call_args
    assert kwargs['sql'] is True

    # Verify verbose option
    mock_alembic_config.return_value.set_main_option.assert_any_call(
        'verbose', 'true'
    )


def test_cli_upgrade_online(mock_alembic_command, mock_alembic_config):
    mock_upgrade, _ = mock_alembic_command
    custom_owner = 'test-owner'
    target_table = 'specific_table'

    # Simulate: a2a-db upgrade head -o test-owner -t specific_table -v
    test_args = [
        'a2a-db',
        'upgrade',
        'head',
        '-o',
        custom_owner,
        '-t',
        target_table,
        '-v',
    ]
    with patch('sys.argv', test_args):
        with patch.dict(os.environ, {'DATABASE_URL': 'sqlite:///test.db'}):
            run_migrations()

    # Verify upgrade was called with sql=False
    args, kwargs = mock_upgrade.call_args
    assert kwargs['sql'] is False

    # Verify options were set in config instance
    mock_alembic_config.return_value.set_main_option.assert_any_call(
        'owner', custom_owner
    )
    mock_alembic_config.return_value.set_main_option.assert_any_call(
        'tables', target_table
    )
    mock_alembic_config.return_value.set_main_option.assert_any_call(
        'verbose', 'true'
    )



def test_cli_downgrade_online(mock_alembic_command, mock_alembic_config):
    _, mock_downgrade = mock_alembic_command
    target_table = 'other_table'

    # Simulate: a2a-db downgrade base -t other_table
    test_args = ['a2a-db', 'downgrade', 'base', '-t', target_table]
    with patch('sys.argv', test_args):
        with patch.dict(os.environ, {'DATABASE_URL': 'sqlite:///test.db'}):
            run_migrations()

    # Verify downgrade was called with sql=False
    args, kwargs = mock_downgrade.call_args
    assert kwargs['sql'] is False

    # Verify tables option
    mock_alembic_config.return_value.set_main_option.assert_any_call(
        'tables', target_table
    )



def test_cli_database_url_flag(mock_alembic_command, mock_alembic_config):
    mock_upgrade, _ = mock_alembic_command
    custom_db = 'sqlite:///custom_cli.db'

    # Simulate: a2a-db -u sqlite:///custom_cli.db
    test_args = ['a2a-db', '-u', custom_db]
    with patch('sys.argv', test_args):
        # Clear environment to ensure it picks up the CLI flag
        with patch.dict(os.environ, {}, clear=True):
            run_migrations()
            # Verify the CLI tool set the environment variable for env.py
            assert os.environ['DATABASE_URL'] == custom_db

    mock_upgrade.assert_called()
