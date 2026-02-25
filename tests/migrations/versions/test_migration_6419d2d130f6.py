import os
import sqlite3
import subprocess
import tempfile
from typing import Generator

import pytest


@pytest.fixture
def temp_db() -> Generator[str, None, None]:
    """Create a temporary SQLite database for testing."""
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    yield path
    if os.path.exists(path):
        os.remove(path)


def test_migration_6419d2d130f6_full_cycle(temp_db: str) -> None:
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

    # 2. Run Upgrade via CLI with a custom owner
    custom_owner = 'test_owner_123'
    env = os.environ.copy()
    env['DATABASE_URL'] = db_url

    # We use the CLI tool to perform the upgrade
    result = subprocess.run(
        [
            'uv',
            'run',
            'a2a-db',
            '--owner',
            custom_owner,
            'upgrade',
            '6419d2d130f6',
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0, f"""Upgrade failed: {result.stderr}
{result.stdout}"""

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

    # 4. Run Downgrade via CLI
    result = subprocess.run(
        ['uv', 'run', 'a2a-db', 'downgrade', 'base'],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0, f"""Downgrade failed: {result.stderr}
{result.stdout}"""

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


def test_migration_6419d2d130f6_idempotency(temp_db: str) -> None:
    """Test that the migration is idempotent (can be run multiple times)."""
    db_url = f'sqlite+aiosqlite:///{temp_db}'

    # 1. Setup initial schema - must include both tables expected by the migration
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

    env = os.environ.copy()
    env['DATABASE_URL'] = db_url

    # 2. Run Upgrade first time
    result = subprocess.run(
        ['uv', 'run', 'a2a-db', 'upgrade', '6419d2d130f6'],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert result.returncode == 0

    # 3. Run Upgrade second time - should not fail even if columns already exist
    # (The migration script has 'if not column_exists' checks)
    result = subprocess.run(
        ['uv', 'run', 'a2a-db', 'upgrade', '6419d2d130f6'],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert result.returncode == 0
