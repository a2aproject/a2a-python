import importlib
import os
import sqlite3
import tempfile

from collections.abc import Generator
from unittest.mock import patch

import pytest

from a2a.a2a_db_cli import run_migrations


# Explicitly import the migration module so it is tracked when Alembic loads it
# dynamically. Revision id starts with a letter, so this import is valid.
try:
    importlib.import_module(
        'a2a.migrations.versions.d7ef24c86b19_mysql_task_timestamp_precision'
    )
except (ImportError, AttributeError):
    pass


REVISION = 'd7ef24c86b19'
PREV_REVISION = 'b5e3d1c8a2f7'


@pytest.fixture(autouse=True)
def mock_logging_config():
    """Prevent tests from mutating global logging state."""
    with patch('logging.basicConfig'), patch('logging.config.fileConfig'):
        yield


def _offline_sql(
    database_url: str,
    command: str,
    revisions: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> str:
    monkeypatch.setenv('DATABASE_URL', database_url)
    with patch(
        'sys.argv',
        ['a2a-db', '--tasks-table', 'agent_tasks', '--sql', command, revisions],
    ):
        run_migrations()
    return capsys.readouterr().out


@pytest.mark.parametrize('dialect', ['mysql', 'mariadb'])
@pytest.mark.parametrize(
    'command, revisions, sql_type',
    [
        ('upgrade', f'{PREV_REVISION}:{REVISION}', 'DATETIME(6)'),
        ('downgrade', f'{REVISION}:{PREV_REVISION}', 'DATETIME'),
    ],
)
def test_migration_d7ef24c86b19_mysql_sql(
    dialect: str,
    command: str,
    revisions: str,
    sql_type: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Both directions alter the configured task table on MySQL dialects."""
    ddl = _offline_sql(
        f'{dialect}+aiomysql://', command, revisions, monkeypatch, capsys
    )
    assert 'ALTER TABLE agent_tasks' in ddl
    assert f'last_updated {sql_type} NULL' in ddl


@pytest.mark.parametrize(
    'database_url', ['postgresql+asyncpg://', 'sqlite+aiosqlite://']
)
def test_migration_d7ef24c86b19_other_dialects_unchanged(
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """PostgreSQL and SQLite already keep microseconds."""
    ddl = _offline_sql(
        database_url,
        'upgrade',
        f'{PREV_REVISION}:{REVISION}',
        monkeypatch,
        capsys,
    )
    assert 'ALTER TABLE' not in ddl


@pytest.fixture
def temp_db() -> Generator[str, None, None]:
    """Create a temporary SQLite database for testing."""
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    yield path
    if os.path.exists(path):
        os.remove(path)


def _run(db_url: str, command: str, revision: str) -> None:
    with patch(
        'sys.argv', ['a2a-db', '--database-url', db_url, command, revision]
    ):
        run_migrations()


def _sqlite_rows(db_path: str, query: str) -> list[tuple]:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(query).fetchall()
    finally:
        conn.close()


def test_migration_d7ef24c86b19_full_cycle_sqlite(temp_db: str) -> None:
    """The revision applies and reverts without altering SQLite tables."""
    conn = sqlite3.connect(temp_db)
    conn.execute(
        'CREATE TABLE tasks (id VARCHAR(36) PRIMARY KEY, '
        'context_id VARCHAR(36) NOT NULL, kind VARCHAR(16) NOT NULL, '
        'status TEXT, artifacts TEXT, history TEXT, metadata TEXT)'
    )
    conn.commit()
    conn.close()
    db_url = f'sqlite+aiosqlite:///{temp_db}'
    _run(db_url, 'upgrade', PREV_REVISION)
    columns = _sqlite_rows(temp_db, 'PRAGMA table_info(tasks)')

    _run(db_url, 'upgrade', REVISION)
    assert _sqlite_rows(temp_db, 'PRAGMA table_info(tasks)') == columns

    _run(db_url, 'downgrade', PREV_REVISION)
    assert _sqlite_rows(temp_db, 'PRAGMA table_info(tasks)') == columns
    assert _sqlite_rows(
        temp_db, 'SELECT version_num FROM a2a_alembic_version'
    ) == [(PREV_REVISION,)]
