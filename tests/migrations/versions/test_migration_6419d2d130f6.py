import logging
import os
import sqlite3
import tempfile
from typing import Generator
from unittest.mock import patch

import pytest

from a2a.a2a_db_cli import run_migrations


@pytest.fixture(autouse=True)
def mock_logging_config():
    """Mock logging configuration function.

    This prevents tests from changing global logging state
    and interfering with other tests (like telemetry tests).
    """
    with patch('logging.basicConfig'), patch('logging.config.fileConfig'):
        yield


@pytest.fixture
def temp_db() -> Generator[str, None, None]:
    """Create a temporary SQLite database for testing."""
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    yield path
    if os.path.exists(path):
        os.remove(path)


def test_migration_6419d2d130f6_full_cycle(
    temp_db: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """Test the full upgrade/downgrade cycle for migration 6419d2d130f6."""
    db_url = f'sqlite+aiosqlite:///{temp_db}'

    # 1. Setup initial schema without the new columns
    conn = sqlite3.connect(temp_db)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE tasks (
            id VARCHAR(36) PRIMARY KEY,
            context_id VARCHAR(36) NOT NULL,
            kind VARCHAR(16) NOT NULL,
            status TEXT,
            artifacts TEXT,
            history TEXT,
            metadata TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE push_notification_configs (
            task_id VARCHAR(36),
            config_id VARCHAR(255),
            config_data BLOB NOT NULL,
            PRIMARY KEY (task_id, config_id)
        )
    """)
    conn.commit()
    conn.close()

    # 2. Run Upgrade via direct call with a custom owner
    custom_owner = 'test_owner_123'

    test_args = [
        'a2a-db',
        '--database-url',
        db_url,
        '--add_columns_owner_last_updated-default-owner',
        custom_owner,
        'upgrade',
        '6419d2d130f6',
    ]
    with patch('sys.argv', test_args):
        run_migrations()

    # 3. Verify columns and index exist
    conn = sqlite3.connect(temp_db)
    cursor = conn.cursor()

    # Check tasks table
    cursor.execute('PRAGMA table_info(tasks)')
    tasks_columns = {row[1]: row for row in cursor.fetchall()}
    assert 'owner' in tasks_columns
    assert 'last_updated' in tasks_columns

    # Check default value for owner in tasks
    # row[4] is dflt_value in PRAGMA table_info
    assert tasks_columns['owner'][4] == f"'{custom_owner}'"

    # Check index on tasks
    cursor.execute('PRAGMA index_list(tasks)')
    tasks_indexes = {row[1] for row in cursor.fetchall()}
    assert 'idx_tasks_owner_last_updated' in tasks_indexes

    # Check push_notification_configs table
    cursor.execute('PRAGMA table_info(push_notification_configs)')
    pnc_columns = {row[1]: row for row in cursor.fetchall()}
    assert 'owner' in pnc_columns
    assert (
        'last_updated' not in pnc_columns
    )  # Only for tables with 'kind' column

    conn.close()

    # 4. Run Downgrade via direct call
    test_args = ['a2a-db', '--database-url', db_url, 'downgrade', 'base']
    with patch('sys.argv', test_args):
        run_migrations()

    # 5. Verify columns are gone
    conn = sqlite3.connect(temp_db)
    cursor = conn.cursor()

    # Check tasks table
    cursor.execute('PRAGMA table_info(tasks)')
    tasks_columns_post = {row[1] for row in cursor.fetchall()}
    assert 'owner' not in tasks_columns_post
    assert 'last_updated' not in tasks_columns_post

    # Check index on tasks
    cursor.execute('PRAGMA index_list(tasks)')
    tasks_indexes_post = {row[1] for row in cursor.fetchall()}
    assert 'idx_tasks_owner_last_updated' not in tasks_indexes_post

    # Check push_notification_configs table
    cursor.execute('PRAGMA table_info(push_notification_configs)')
    pnc_columns_post = {row[1] for row in cursor.fetchall()}
    assert 'owner' not in pnc_columns_post

    conn.close()


def test_migration_6419d2d130f6_custom_tables(
    temp_db: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """Test the migration with custom table names."""
    db_url = f'sqlite+aiosqlite:///{temp_db}'
    custom_tasks = 'custom_tasks'
    custom_push = 'custom_push'

    # 1. Setup initial schema with custom names
    conn = sqlite3.connect(temp_db)
    cursor = conn.cursor()
    cursor.execute(
        f'CREATE TABLE {custom_tasks} (id VARCHAR(36) PRIMARY KEY, kind VARCHAR(16))'
    )
    cursor.execute(
        f'CREATE TABLE {custom_push} (task_id VARCHAR(36), PRIMARY KEY (task_id))'
    )
    conn.commit()
    conn.close()

    # 2. Run Upgrade via direct call with custom table flags
    test_args = [
        'a2a-db',
        '--database-url',
        db_url,
        '--tasks-table',
        custom_tasks,
        '--push-notification-table',
        custom_push,
        'upgrade',
        '6419d2d130f6',
    ]
    with patch('sys.argv', test_args):
        run_migrations()

    # 3. Verify columns exist in custom tables
    conn = sqlite3.connect(temp_db)
    cursor = conn.cursor()

    cursor.execute(f'PRAGMA table_info({custom_tasks})')
    assert 'owner' in {row[1] for row in cursor.fetchall()}

    cursor.execute(f'PRAGMA table_info({custom_push})')
    assert 'owner' in {row[1] for row in cursor.fetchall()}

    conn.close()


def test_migration_6419d2d130f6_missing_tables(
    temp_db: str, caplog: pytest.LogCaptureFixture
) -> None:
    """Test that the migration handles missing tables gracefully."""
    db_url = f'sqlite+aiosqlite:///{temp_db}'

    # Run upgrade on empty database
    test_args = [
        'a2a-db',
        '--database-url',
        db_url,
        'upgrade',
        '6419d2d130f6',
    ]
    with patch('sys.argv', test_args), caplog.at_level(logging.WARNING):
        run_migrations()

    assert "Table 'tasks' does not exist" in caplog.text


def test_migration_6419d2d130f6_idempotency(
    temp_db: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """Test that the migration is idempotent (can be run multiple times)."""
    db_url = f'sqlite+aiosqlite:///{temp_db}'

    # 1. Setup initial schema
    conn = sqlite3.connect(temp_db)
    cursor = conn.cursor()
    cursor.execute(
        'CREATE TABLE tasks (id VARCHAR(36) PRIMARY KEY, kind VARCHAR(16))'
    )
    cursor.execute(
        'CREATE TABLE push_notification_configs (task_id VARCHAR(36), config_id VARCHAR(255), PRIMARY KEY (task_id, config_id))'
    )
    conn.commit()
    conn.close()

    # 2. Run Upgrade first time
    test_args = [
        'a2a-db',
        '--database-url',
        db_url,
        'upgrade',
        '6419d2d130f6',
    ]
    with patch('sys.argv', test_args):
        run_migrations()

    # 3. Run Upgrade second time - should not fail even if columns already exist
    with patch('sys.argv', test_args):
        run_migrations()


def test_migration_6419d2d130f6_offline(
    temp_db: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """Test that offline mode generates the expected SQL without modifying the database."""
    db_url = f'sqlite+aiosqlite:///{temp_db}'

    # Run upgrade in offline mode
    test_args = [
        'a2a-db',
        '--database-url',
        db_url,
        '--sql',
        'upgrade',
        '6419d2d130f6',
    ]
    with patch('sys.argv', test_args):
        run_migrations()

    captured = capsys.readouterr()
    # Verify SQL output contains key migration statements
    assert 'ALTER TABLE tasks ADD COLUMN owner' in captured.out
    assert 'CREATE INDEX idx_tasks_owner_last_updated' in captured.out
    assert 'CREATE TABLE alembic_version' in captured.out

    # Verify the database was NOT actually changed (since it is offline mode)
    conn = sqlite3.connect(temp_db)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in cursor.fetchall()}
    # alembic_version and tasks should not exist because we didn't run the SQL
    assert 'tasks' not in tables
    assert 'alembic_version' not in tables
    conn.close()
