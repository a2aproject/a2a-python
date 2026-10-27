import importlib
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
        'a2a.migrations.versions.b0f01b04ff26_add_task_leases'
    )
except (ImportError, AttributeError):
    pass


REVISION = 'b0f01b04ff26'
PREV_REVISION = 'b5e3d1c8a2f7'


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


def _tables(db_path: str) -> set[str]:
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute(
            "SELECT name FROM sqlite_schema WHERE type='table'"
        )
        return {row[0] for row in cursor.fetchall()}
    finally:
        conn.close()


def test_migration_b0f01b04ff26_full_cycle(temp_db: str) -> None:
    """Upgrade creates task_leases; downgrade removes only that table."""
    db_url = f'sqlite+aiosqlite:///{temp_db}'
    _setup_initial_schema(temp_db)

    _upgrade(db_url)

    conn = sqlite3.connect(temp_db)
    cursor = conn.cursor()
    cursor.execute('PRAGMA table_info(task_leases)')
    assert {row[1] for row in cursor.fetchall()} == {
        'task_id',
        'owner',
        'token',
        'expires_at_ms',
    }
    cursor.execute('PRAGMA index_list(task_leases)')
    assert 'ix_task_leases_expires_at_ms' in {
        row[1] for row in cursor.fetchall()
    }
    conn.close()

    with patch(
        'sys.argv',
        ['a2a-db', '--database-url', db_url, 'downgrade', PREV_REVISION],
    ):
        run_migrations()

    tables = _tables(temp_db)
    assert 'task_leases' not in tables
    assert {'task_versions', 'task_events'} <= tables


def test_migration_b0f01b04ff26_idempotency(temp_db: str) -> None:
    """Running the upgrade twice must not fail."""
    db_url = f'sqlite+aiosqlite:///{temp_db}'
    _setup_initial_schema(temp_db)
    _upgrade(db_url)
    _upgrade(db_url)

    assert 'task_leases' in _tables(temp_db)


def test_migration_b0f01b04ff26_offline(
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
    assert 'CREATE TABLE task_leases' in out
    assert 'ix_task_leases_expires_at_ms' in out
    assert 'task_leases' not in _tables(temp_db)
