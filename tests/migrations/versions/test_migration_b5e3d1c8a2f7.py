import importlib
import logging
import os
import sqlite3
import tempfile

from typing import Generator
from unittest.mock import patch

import pytest

from a2a.a2a_db_cli import run_migrations


# Explicitly import the migration module so it is tracked when Alembic loads it
# dynamically. Revision id starts with a letter, so this import is valid.
try:
    importlib.import_module(
        'a2a.migrations.versions.b5e3d1c8a2f7_add_task_version_and_task_events'
    )
except (ImportError, AttributeError):
    pass


REVISION = 'b5e3d1c8a2f7'
PREV_REVISION = '38ce57e08137'


@pytest.fixture(autouse=True)
def mock_logging_config():
    """Prevent tests from mutating global logging state."""
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


def _setup_initial_schema(db_path: str) -> None:
    """Create the base tasks/push tables the earlier migrations expect."""
    conn = sqlite3.connect(db_path)
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


def _upgrade(db_url: str, revision: str = REVISION) -> None:
    with patch(
        'sys.argv',
        ['a2a-db', '--database-url', db_url, 'upgrade', revision],
    ):
        run_migrations()


def test_migration_b5e3d1c8a2f7_full_cycle(temp_db: str) -> None:
    """Upgrade adds tasks.version + task_events; downgrade reverses it."""
    db_url = f'sqlite+aiosqlite:///{temp_db}'
    _setup_initial_schema(temp_db)

    # Upgrade through the whole chain up to this revision.
    _upgrade(db_url)

    conn = sqlite3.connect(temp_db)
    cursor = conn.cursor()

    # tasks.version exists.
    cursor.execute('PRAGMA table_info(tasks)')
    tasks_columns = {row[1] for row in cursor.fetchall()}
    assert 'version' in tasks_columns

    # task_events table exists with the expected columns.
    cursor.execute('PRAGMA table_info(task_events)')
    event_columns = {row[1] for row in cursor.fetchall()}
    assert event_columns == {
        'seq',
        'task_id',
        'owner',
        'task_version',
        'event_data',
    }

    # Index on task_id exists.
    cursor.execute('PRAGMA index_list(task_events)')
    event_indexes = {row[1] for row in cursor.fetchall()}
    assert 'ix_task_events_task_id' in event_indexes
    conn.close()

    # Downgrade one step: task_events and tasks.version are gone.
    with patch(
        'sys.argv',
        ['a2a-db', '--database-url', db_url, 'downgrade', PREV_REVISION],
    ):
        run_migrations()

    conn = sqlite3.connect(temp_db)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_schema WHERE type='table'")
    tables = {row[0] for row in cursor.fetchall()}
    assert 'task_events' not in tables

    cursor.execute('PRAGMA table_info(tasks)')
    tasks_columns_post = {row[1] for row in cursor.fetchall()}
    assert 'version' not in tasks_columns_post
    conn.close()


def test_migration_b5e3d1c8a2f7_backfills_existing_rows(temp_db: str) -> None:
    """Rows that predate the migration get a non-NULL version (backfill).

    A NULL version reads back as TaskVersion.MISSING, which would route the
    versioned store's save into the first-write INSERT path and permanently
    wedge a pre-migration in-flight task. The migration backfills to 1.
    """
    db_url = f'sqlite+aiosqlite:///{temp_db}'
    _setup_initial_schema(temp_db)

    # A task that already exists before the migration runs.
    conn = sqlite3.connect(temp_db)
    conn.execute(
        'INSERT INTO tasks (id, context_id, kind, status) VALUES (?, ?, ?, ?)',
        ('legacy-1', 'ctx-1', 'task', '{}'),
    )
    conn.commit()
    conn.close()

    _upgrade(db_url)

    conn = sqlite3.connect(temp_db)
    cursor = conn.cursor()
    cursor.execute("SELECT version FROM tasks WHERE id = 'legacy-1'")
    (version,) = cursor.fetchone()
    conn.close()
    assert version == 1  # backfilled, not NULL


def test_migration_b5e3d1c8a2f7_idempotency(temp_db: str) -> None:
    """Running the upgrade twice must not fail."""
    db_url = f'sqlite+aiosqlite:///{temp_db}'
    _setup_initial_schema(temp_db)
    _upgrade(db_url)
    # Second run: version column and task_events table already exist.
    _upgrade(db_url)

    conn = sqlite3.connect(temp_db)
    cursor = conn.cursor()
    cursor.execute('PRAGMA table_info(task_events)')
    assert {row[1] for row in cursor.fetchall()}  # table still present
    conn.close()


def test_migration_b5e3d1c8a2f7_missing_tasks_table(
    temp_db: str, caplog: pytest.LogCaptureFixture
) -> None:
    """A missing tasks table is handled gracefully (warning, no crash)."""
    db_url = f'sqlite+aiosqlite:///{temp_db}'
    # No initial schema: the tasks table does not exist.
    with caplog.at_level(logging.WARNING):
        _upgrade(db_url)
    assert "Table 'tasks' does not exist" in caplog.text
    # task_events is independent of tasks and is still created.
    conn = sqlite3.connect(temp_db)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_schema WHERE type='table'")
    tables = {row[0] for row in cursor.fetchall()}
    assert 'task_events' in tables
    conn.close()


def test_migration_b5e3d1c8a2f7_offline(
    temp_db: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """Offline (--sql) mode emits the DDL without touching the database."""
    db_url = f'sqlite+aiosqlite:///{temp_db}'
    _setup_initial_schema(temp_db)

    with patch(
        'sys.argv',
        ['a2a-db', '--database-url', db_url, '--sql', 'upgrade', REVISION],
    ):
        run_migrations()

    out = capsys.readouterr().out
    assert 'ALTER TABLE tasks ADD COLUMN version' in out
    assert 'UPDATE tasks SET version = 1 WHERE version IS NULL' in out
    assert 'CREATE TABLE task_events' in out
    assert 'ix_task_events_task_id' in out

    # The database itself was not modified.
    conn = sqlite3.connect(temp_db)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_schema WHERE type='table'")
    tables = {row[0] for row in cursor.fetchall()}
    assert 'task_events' not in tables
    cursor.execute('PRAGMA table_info(tasks)')
    assert 'version' not in {row[1] for row in cursor.fetchall()}
    conn.close()
