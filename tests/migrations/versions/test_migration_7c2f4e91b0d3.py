import importlib
import os
import sqlite3
import tempfile

from collections.abc import Generator
from unittest.mock import patch

import pytest

from a2a.a2a_db_cli import run_migrations


try:
    importlib.import_module(
        'a2a.migrations.versions.7c2f4e91b0d3_add_generation'
    )
except (ImportError, AttributeError):
    pass


REVISION = '7c2f4e91b0d3'
PREV_REVISION = 'b5e3d1c8a2f7'


@pytest.fixture(autouse=True)
def mock_logging_config():
    """Prevent tests from mutating global logging state."""
    with patch('logging.basicConfig'), patch('logging.config.fileConfig'):
        yield


@pytest.fixture
def temp_db() -> Generator[str, None, None]:
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    yield path
    if os.path.exists(path):
        os.remove(path)


def _run(db_url: str, *args: str) -> None:
    with patch('sys.argv', ['a2a-db', '--database-url', db_url, *args]):
        run_migrations()


def _setup_previous_head(db_path: str, db_url: str) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute("""
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
    conn.execute("""
        CREATE TABLE push_notification_configs (
            task_id VARCHAR(36),
            config_id VARCHAR(255),
            config_data BLOB NOT NULL,
            PRIMARY KEY (task_id, config_id)
        )
    """)
    conn.commit()
    conn.close()
    _run(db_url, 'upgrade', PREV_REVISION)

    conn = sqlite3.connect(db_path)
    conn.executemany(
        'INSERT INTO tasks (id, context_id, kind, owner) VALUES (?, ?, ?, ?)',
        [('t-versioned', 'c', 'task', 'alice'), ('t-plain', 'c', 'task', None)],
    )
    conn.execute(
        'INSERT INTO task_versions (task_id, owner, version) VALUES (?, ?, ?)',
        ('t-versioned', 'alice', 7),
    )
    conn.executemany(
        'INSERT INTO task_events (task_id, owner, task_version, event_data) '
        'VALUES (?, ?, ?, ?)',
        [('t-versioned', 'alice', 6, b''), ('t-versioned', 'alice', 7, b'')],
    )
    conn.commit()
    conn.close()


def _columns(db_path: str, table: str) -> set[str]:
    conn = sqlite3.connect(db_path)
    try:
        return {row[1] for row in conn.execute(f'PRAGMA table_info({table})')}
    finally:
        conn.close()


def test_migration_7c2f4e91b0d3_backfills_and_downgrades(temp_db: str) -> None:
    db_url = f'sqlite+aiosqlite:///{temp_db}'
    _setup_previous_head(temp_db, db_url)

    _run(db_url, 'upgrade', REVISION)

    conn = sqlite3.connect(temp_db)
    assert dict(conn.execute('SELECT id, generation FROM tasks')) == {
        't-versioned': 7,
        't-plain': 1,
    }
    assert [
        row[0]
        for row in conn.execute(
            'SELECT generation FROM task_events ORDER BY seq'
        )
    ] == [6, 7]
    conn.close()

    _run(db_url, 'downgrade', PREV_REVISION)

    assert 'generation' not in _columns(temp_db, 'tasks')
    assert 'generation' not in _columns(temp_db, 'task_events')


def test_migration_7c2f4e91b0d3_is_idempotent(temp_db: str) -> None:
    db_url = f'sqlite+aiosqlite:///{temp_db}'
    _setup_previous_head(temp_db, db_url)
    _run(db_url, 'upgrade', REVISION)
    _run(db_url, 'upgrade', REVISION)
    assert 'generation' in _columns(temp_db, 'tasks')
    assert 'generation' in _columns(temp_db, 'task_events')


def test_migration_7c2f4e91b0d3_offline(
    temp_db: str, capsys: pytest.CaptureFixture[str]
) -> None:
    db_url = f'sqlite+aiosqlite:///{temp_db}'
    _setup_previous_head(temp_db, db_url)
    _run(db_url, '--sql', 'upgrade', f'{PREV_REVISION}:{REVISION}')
    out = capsys.readouterr().out
    assert 'ALTER TABLE tasks ADD COLUMN generation' in out
    assert 'ALTER TABLE task_events ADD COLUMN generation' in out
    assert 'generation' not in _columns(temp_db, 'tasks')
