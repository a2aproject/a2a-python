"""Utility functions for Alembic migrations."""

import logging
from typing import Any

import sqlalchemy as sa

try:
    from alembic import context, op
except ImportError as e:
    raise ImportError(
        "A2A migrations require the 'db-cli' extra. Install with: 'pip install a2a-sdk[db-cli]'."
    ) from e


def _get_inspector() -> sa.engine.reflection.Inspector:
    """Get the current database inspector."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return inspector


def table_exists(table_name: str) -> bool:
    """Check if a table exists in the database."""
    if context.is_offline_mode():
        return True
    inspector = _get_inspector()
    return table_name in inspector.get_table_names()


def column_exists(
    table_name: str, column_name: str, downgrade_mode: bool = False
) -> bool:
    """Check if a column exists in a table."""
    if context.is_offline_mode():
        return downgrade_mode

    inspector = _get_inspector()
    columns = [c['name'] for c in inspector.get_columns(table_name)]
    return column_name in columns


def index_exists(
    table_name: str, index_name: str, downgrade_mode: bool = False
) -> bool:
    """Check if an index exists on a table."""
    if context.is_offline_mode():
        return downgrade_mode

    inspector = _get_inspector()
    indexes = [i['name'] for i in inspector.get_indexes(table_name)]
    return index_name in indexes


def add_column(
    table: str,
    column_name: str,
    nullable: bool,
    type_: sa.types.TypeEngine,
    default: Any | None = None,
) -> None:
    """Add a column to a table if it doesn't already exist."""
    if not column_exists(table, column_name):
        op.add_column(
            table,
            sa.Column(
                column_name,
                type_,
                nullable=nullable,
                server_default=default,
            ),
        )
    else:
        logging.info(
            f"Column '{column_name}' already exists in table '{table}'. Skipping."
        )


def drop_column(table: str, column_name: str) -> None:
    """Drop a column from a table if it exists."""
    if column_exists(table, column_name, True):
        op.drop_column(table, column_name)
    else:
        logging.info(
            f"Column '{column_name}' does not exist in table '{table}'. Skipping."
        )


def add_index(table: str, index_name: str, columns: list[str]) -> None:
    """Create an index on a table if it doesn't already exist."""
    if not index_exists(table, index_name):
        op.create_index(
            index_name,
            table,
            columns,
        )
    else:
        logging.info(
            f"Index '{index_name}' already exists on table '{table}'. Skipping."
        )


def drop_index(table: str, index_name: str) -> None:
    """Drop an index from a table if it exists."""
    if index_exists(table, index_name, True):
        op.drop_index(index_name, table_name=table)
    else:
        logging.info(
            f"Index '{index_name}' does not exist on table '{table}'. Skipping."
        )
