"""add column protocol version

Revision ID: 38ce57e08137
Revises: 6419d2d130f6
Create Date: 2026-03-09 12:07:16.998955

"""

import logging
from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa

try:
    from alembic import context
except ImportError as e:
    raise ImportError(
        "A2A migrations require the 'db-cli' extra. Install with: 'pip install a2a-sdk[db-cli]'."
    ) from e

from a2a.migrations.migration_utils import table_exists, add_column, drop_column


# revision identifiers, used by Alembic.
revision: str = '38ce57e08137'
down_revision: Union[str, Sequence[str], None] = '6419d2d130f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    tasks_table = context.config.get_main_option('tasks_table', 'tasks')
    push_notification_configs_table = context.config.get_main_option(
        'push_notification_configs_table', 'push_notification_configs'
    )

    if table_exists(tasks_table):
        add_column(tasks_table, 'protocol_version', True, sa.String(16))
    else:
        logging.warning(
            f"Table '{tasks_table}' does not exist. Skipping upgrade for this table."
        )

    if table_exists(push_notification_configs_table):
        add_column(
            push_notification_configs_table,
            'protocol_version',
            True,
            sa.String(16),
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
        drop_column(tasks_table, 'protocol_version')
    else:
        logging.warning(
            f"Table '{tasks_table}' does not exist. Skipping downgrade for this table."
        )

    if table_exists(push_notification_configs_table):
        drop_column(push_notification_configs_table, 'protocol_version')
    else:
        logging.warning(
            f"Table '{push_notification_configs_table}' does not exist. Skipping downgrade for this table."
        )
