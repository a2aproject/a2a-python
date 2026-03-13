"""add_columns_owner_last_updated.

Revision ID: 6419d2d130f6
Revises:
Create Date: 2026-02-17 09:23:06.758085

"""

import logging
from collections.abc import Sequence

import sqlalchemy as sa

try:
    from alembic import context
except ImportError as e:
    raise ImportError(
        "'Add columns owner and last_updated to database tables' migration requires Alembic. Install with: 'pip install a2a-sdk[db-cli]'."
    ) from e

from a2a.migrations.migration_utils import (
    table_exists,
    add_column,
    add_index,
    drop_column,
    drop_index,
)


# revision identifiers, used by Alembic.
revision: str = '6419d2d130f6'
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Get the default value from the config (passed via CLI)
    owner = context.config.get_main_option(
        'add_columns_owner_last_updated_default_owner',
        'legacy_v03_no_user_info',
    )
    tasks_table = context.config.get_main_option('tasks_table', 'tasks')
    push_notification_configs_table = context.config.get_main_option(
        'push_notification_configs_table', 'push_notification_configs'
    )

    if table_exists(tasks_table):
        add_column(tasks_table, 'owner', True, sa.String(255), owner)
        add_column(tasks_table, 'last_updated', True, sa.DateTime())
        add_index(
            tasks_table,
            f'idx_{tasks_table}_owner_last_updated',
            ['owner', 'last_updated'],
        )
    else:
        logging.warning(
            f"Table '{tasks_table}' does not exist. Skipping upgrade for this table."
        )

    if table_exists(push_notification_configs_table):
        add_column(
            push_notification_configs_table,
            'owner',
            True,
            sa.String(255),
            owner,
        )
        add_index(
            push_notification_configs_table,
            f'ix_{push_notification_configs_table}_owner',
            ['owner'],
        )
    else:
        logging.warning(
            f"Table '{push_notification_configs_table}' does not exist. Skipping upgrade for this table."
        )


def downgrade() -> None:
    """Downgrade schema."""
    tasks_table = context.config.get_main_option('tasks_table', 'tasks')
    push_notification_configs_table = context.config.get_main_option(
        'push_notification_configs_table', 'push_notification_configs'
    )

    if table_exists(tasks_table):
        drop_index(
            tasks_table,
            f'idx_{tasks_table}_owner_last_updated',
        )
        drop_column(tasks_table, 'owner')
        drop_column(tasks_table, 'last_updated')
    else:
        logging.warning(
            f"Table '{tasks_table}' does not exist. Skipping downgrade for this table."
        )

    if table_exists(push_notification_configs_table):
        drop_index(
            push_notification_configs_table,
            f'ix_{push_notification_configs_table}_owner',
        )
        drop_column(push_notification_configs_table, 'owner')
    else:
        logging.warning(
            f"Table '{push_notification_configs_table}' does not exist. Skipping downgrade for this table."
        )
