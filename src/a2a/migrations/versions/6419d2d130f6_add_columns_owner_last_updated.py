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
        "'Add columns owner and last_updated to database tables' migration requires Alembic. Install with: 'pip install a2a-sdk[db-cli]'."
    ) from e


# revision identifiers, used by Alembic.
revision: str = '6419d2d130f6'
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _get_inspector() -> sa.engine.reflection.Inspector:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return inspector


def _add_column(
    table: str,
    column_name: str,
    type_: sa.types.TypeEngine,
    value: str | None = None,
) -> None:
    if not _column_exists(table, column_name):
        op.add_column(
            table,
            sa.Column(
                column_name,
                type_,
                nullable=False,
                server_default=value,
            ),
        )


def _add_index(table: str, index_name: str, columns: list[str]) -> None:
    if not _index_exists(table, index_name):
        op.create_index(
            index_name,
            table,
            columns,
        )


def _drop_column(table: str, column_name: str) -> None:
    if _column_exists(table, column_name, True):
        op.drop_column(table, column_name)


def _drop_index(table: str, index_name: str) -> None:
    if _index_exists(table, index_name, True):
        op.drop_index(index_name, table_name=table)


def _table_exists(table_name: str) -> bool:
    if context.is_offline_mode():
        return True
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def _column_exists(
    table_name: str, column_name: str, downgrade_mode: bool = False
) -> bool:
    if context.is_offline_mode():
        return downgrade_mode

    inspector = _get_inspector()
    columns = [c['name'] for c in inspector.get_columns(table_name)]
    return column_name in columns


def _index_exists(
    table_name: str, index_name: str, downgrade_mode: bool = False
) -> bool:
    if context.is_offline_mode():
        return downgrade_mode

    inspector = _get_inspector()
    indexes = [i['name'] for i in inspector.get_indexes(table_name)]
    return index_name in indexes


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

    if _table_exists(tasks_table):
        _add_column(tasks_table, 'owner', sa.String(128), owner)
        _add_column(tasks_table, 'last_updated', sa.DateTime(timezone=True))
        _add_index(
            tasks_table,
            f'idx_{tasks_table}_owner_last_updated',
            ['owner', 'last_updated'],
        )
    else:
        logging.warning(
            f"Table '{tasks_table}' does not exist. Skipping upgrade for this table."
        )

    if _table_exists(push_notification_configs_table):
        _add_column(
            push_notification_configs_table, 'owner', sa.String(128), owner
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

    if _table_exists(tasks_table):
        _drop_index(
            tasks_table,
            f'idx_{tasks_table}_owner_last_updated',
        )
        _drop_column(tasks_table, 'owner')
        _drop_column(tasks_table, 'last_updated')
    else:
        logging.warning(
            f"Table '{tasks_table}' does not exist. Skipping downgrade for this table."
        )

    if _table_exists(push_notification_configs_table):
        _drop_column(push_notification_configs_table, 'owner')
    else:
        logging.warning(
            f"Table '{push_notification_configs_table}' does not exist. Skipping downgrade for this table."
        )
