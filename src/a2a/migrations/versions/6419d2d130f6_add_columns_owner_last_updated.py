"""add_columns_owner_last_updated.

Revision ID: 6419d2d130f6
Revises:
Create Date: 2026-02-17 09:23:06.758085

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import context, op


# revision identifiers, used by Alembic.
revision: str = '6419d2d130f6'
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def column_exists(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = [c['name'] for c in inspector.get_columns(table_name)]
    return column_name in columns


def index_exists(table_name: str, index_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    indexes = [i['name'] for i in inspector.get_indexes(table_name)]
    return index_name in indexes


def upgrade() -> None:
    """Upgrade schema."""
    # Get the default value from the config (passed via CLI)
    owner = context.config.get_main_option('owner', 'unknown')
    tables_str = context.config.get_main_option(
        'tables', 'tasks,push_notification_configs'
    )
    tables = [t.strip() for t in tables_str.split(',')]

    for table in tables:
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
        if column_exists(
            table, 'kind'
        ):  # Check to differentiate between table of tasks and push_notification_configs. Only tasks table should have last_updated column and index.
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


def downgrade() -> None:
    """Downgrade schema."""
    tables_str = context.config.get_main_option(
        'tables', 'tasks,push_notification_configs'
    )
    tables = [t.strip() for t in tables_str.split(',')]

    for table in tables:
        if index_exists(table, f'idx_{table}_owner_last_updated'):
            op.drop_index(f'idx_{table}_owner_last_updated', table_name=table)
        if column_exists(table, 'owner'):
            op.drop_column(table, 'owner')
        if column_exists(table, 'last_updated'):
            op.drop_column(table, 'last_updated')
