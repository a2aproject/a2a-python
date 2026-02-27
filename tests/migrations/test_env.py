import asyncio
import importlib
import logging
import os
import sys
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def mock_alembic_setup():
    """Fixture to mock alembic context and config for safe import of env.py."""
    with patch('alembic.context') as mock_context:
        mock_config = MagicMock()
        mock_context.config = mock_config
        # Basic setup to avoid crashes on import
        mock_config.config_file_name = 'alembic.ini'
        mock_config.get_section.return_value = {}

        # We need to make sure 'a2a.migrations.env' is not in sys.modules
        # initially so we can control its execution
        if 'a2a.migrations.env' in sys.modules:
            del sys.modules['a2a.migrations.env']

        yield mock_context, mock_config


def test_env_py_missing_db_url(mock_alembic_setup):
    """Test that env.py raises RuntimeError when DATABASE_URL is missing."""
    mock_context, mock_config = mock_alembic_setup

    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(
            RuntimeError, match='DATABASE_URL environment variable is not set'
        ):
            # Using standard import/reload ensures coverage tracking
            import a2a.migrations.env as env

            importlib.reload(env)


def test_env_py_offline_mode(mock_alembic_setup):
    """Test env.py logic in offline mode."""
    mock_context, mock_config = mock_alembic_setup
    db_url = 'sqlite+aiosqlite:///test_cov_offline.db'

    mock_config.config_file_name = None  # Skip fileConfig
    mock_context.is_offline_mode.return_value = True

    # Mock get_main_option to return db_url for 'sqlalchemy.url'
    def get_opt(key, default=None):
        if key == 'sqlalchemy.url':
            return db_url
        return default

    mock_config.get_main_option.side_effect = get_opt

    with patch.dict(os.environ, {'DATABASE_URL': db_url}):
        import a2a.migrations.env as env

        importlib.reload(env)

    # Verify sqlalchemy.url was set from env var
    mock_config.set_main_option.assert_any_call('sqlalchemy.url', db_url)

    # Verify context.configure was called for offline mode
    mock_context.configure.assert_called()
    # Check if url was passed to configure
    args, kwargs = mock_context.configure.call_args
    assert kwargs['url'] == db_url


@patch('alembic.context.run_migrations')
@patch('sqlalchemy.ext.asyncio.async_engine_from_config')
@patch('asyncio.run')
def test_env_py_online_mode(
    mock_asyncio_run,
    mock_async_engine,
    mock_run_migrations,
    mock_alembic_setup,
):
    """Test env.py logic in online mode."""
    mock_context, mock_config = mock_alembic_setup
    db_url = 'sqlite+aiosqlite:///test_cov_online.db'

    mock_config.config_file_name = None
    mock_context.is_offline_mode.return_value = False

    # Prevent "coroutine never awaited" warning
    def close_coro(coro):
        if asyncio.iscoroutine(coro):
            coro.close()

    mock_asyncio_run.side_effect = close_coro

    with patch.dict(os.environ, {'DATABASE_URL': db_url}):
        import a2a.migrations.env as env

        importlib.reload(env)

    # Verify sqlalchemy.url was set
    mock_config.set_main_option.assert_any_call('sqlalchemy.url', db_url)

    # Verify asyncio.run was called to start online migrations
    mock_asyncio_run.assert_called()


def test_env_py_verbose_logging(mock_alembic_setup):
    """Test that env.py enables verbose logging when 'verbose' option is set."""
    mock_context, mock_config = mock_alembic_setup
    db_url = 'sqlite+aiosqlite:///test_cov_verbose.db'

    # Use a real side_effect to simulate config.get_main_option
    def get_opt(key, default=None):
        if key == 'verbose':
            return 'true'
        if key == 'sqlalchemy.url':
            return db_url
        return default

    mock_config.get_main_option.side_effect = get_opt
    mock_config.config_file_name = None
    mock_context.is_offline_mode.return_value = True

    with patch('logging.getLogger') as mock_get_logger:
        mock_logger = MagicMock()
        mock_get_logger.return_value = mock_logger

        with patch.dict(os.environ, {'DATABASE_URL': db_url}):
            import a2a.migrations.env as env

            importlib.reload(env)

        # Check if sqlalchemy.engine logger level was set to INFO
        mock_get_logger.assert_called_with('sqlalchemy.engine')
        mock_logger.setLevel.assert_called_with(logging.INFO)
