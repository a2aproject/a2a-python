"""add_columns_owner_last_updated.

Revision ID: 6419d2d130f6
Revises:
Create Date: 2026-02-17 09:23:06.758085

"""

from collections.abc import Sequence

import logging
import sqlalchemy as sa

try:
    from alembic import context, op
except ImportError as e:
    raise ImportError(
        "Add columns to database tables migration requires Alembic. Install with: 'pip install a2a-sdk[a2a-db-cli]'."
    ) from e


# revision identifiers, used by Alembic.
revision: str = '6419d2d130f6'
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def table_exists(table_name: str) -> bool:
    if context.is_offline_mode():
        return True
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def column_exists(table_name: str, column_name: str) -> bool:
    if context.is_offline_mode():
        return False
    if not table_exists(table_name):
        return False
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = [c['name'] for c in inspector.get_columns(table_name)]
    return column_name in columns


def index_exists(table_name: str, index_name: str) -> bool:
    if context.is_offline_mode():
        return False
    if not table_exists(table_name):
        return False
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    indexes = [i['name'] for i in inspector.get_indexes(table_name)]
    return index_name in indexes


def upgrade() -> None:
    """Upgrade schema."""
    # Get the default value from the config (passed via CLI)
    owner = context.config.get_main_option('owner', 'legacy_v03_no_user_info')
    tasks_tables = ['tasks']
    push_notification_tables = ['push_notification_configs']

    if tasks_tables_str := context.config.get_main_option('tasks_tables', None):
        tasks_tables.extend([t.strip() for t in tasks_tables_str.split(',')])
    if push_notification_tables_str := context.config.get_main_option(
        'push_notification_tables', None
    ):
        push_notification_tables.extend(
            [t.strip() for t in push_notification_tables_str.split(',')]
        )

    for table in tasks_tables + push_notification_tables:
        if table_exists(table):
            if not column_exists(table, 'owner'):
                op.add_column(
                    table,
                    sa.Column(
                        'owner',
                        sa.String(128),
                        nullable=False,
                        server_default=owner,
                    ),
                )
        else:
            logging.warning(
                f"Table '{table}' does not exist. Skipping upgrade for this table."
            )
    for table in tasks_tables:
        if table_exists(table):
            if not column_exists(table, 'last_updated'):
                op.add_column(
                    table,
                    sa.Column('last_updated', sa.String(22), nullable=True),
                )
            if not index_exists(table, f'idx_{table}_owner_last_updated'):
                op.create_index(
                    f'idx_{table}_owner_last_updated',
                    table,
                    ['owner', 'last_updated'],
                )
        else:
            logging.warning(
                f"Table '{table}' does not exist. Skipping upgrade for this table."
            )


def downgrade() -> None:
    """Downgrade schema."""
    tables = ['tasks', 'push_notification_configs']

    if tasks_tables_str := context.config.get_main_option('tasks_tables', None):
        tables.extend([t.strip() for t in tasks_tables_str.split(',')])
    if push_notification_tables_str := context.config.get_main_option(
        'push_notification_tables', None
    ):
        tables.extend(
            [t.strip() for t in push_notification_tables_str.split(',')]
        )

    for table in tables:
        if table_exists(table):
            if index_exists(table, f'idx_{table}_owner_last_updated'):
                op.drop_index(
                    f'idx_{table}_owner_last_updated', table_name=table
                )
            if column_exists(table, 'owner'):
                op.drop_column(table, 'owner')
            if column_exists(table, 'last_updated'):
                op.drop_column(table, 'last_updated')
        else:
            logging.warning(
                f"Table '{table}' does not exist. Skipping downgrade for this table."
            )
