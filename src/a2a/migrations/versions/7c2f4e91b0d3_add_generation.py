"""add generation to tasks and task_events

Revision ID: 7c2f4e91b0d3
Revises: b5e3d1c8a2f7
Create Date: 2026-11-24 12:00:00.000000

"""

import logging

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa


try:
    from alembic import context, op
except ImportError as e:
    raise ImportError(
        "A2A migrations require the 'db-cli' extra. Install with: 'pip install a2a-sdk[db-cli]'."
    ) from e

from a2a.migrations.migration_utils import (
    add_column,
    drop_column,
    table_exists,
)


# revision identifiers, used by Alembic.
revision: str = '7c2f4e91b0d3'
down_revision: Union[str, Sequence[str], None] = 'b5e3d1c8a2f7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_names() -> tuple[str, str, str]:
    config = context.config
    return (
        config.get_main_option('tasks_table', 'tasks'),
        config.get_main_option('task_events_table', 'task_events'),
        config.get_main_option('task_versions_table', 'task_versions'),
    )


def upgrade() -> None:
    """Add `generation` columns and backfill them.

    Every write so far bumped the task version and logged at most one event,
    so the version is the best available stand-in for the generation.
    """
    tasks_table, events_table, versions_table = _table_names()

    if table_exists(tasks_table):
        add_column(tasks_table, 'generation', True, sa.BigInteger())
        tasks = sa.table(
            tasks_table,
            sa.column('id', sa.String),
            sa.column('owner', sa.String),
            sa.column('generation', sa.BigInteger),
        )
        backfill: sa.ColumnElement = sa.literal(1)
        if table_exists(versions_table):
            versions = sa.table(
                versions_table,
                sa.column('task_id', sa.String),
                sa.column('owner', sa.String),
                sa.column('version', sa.BigInteger),
            )
            backfill = sa.func.coalesce(
                sa.select(versions.c.version)
                .where(
                    versions.c.task_id == tasks.c.id,
                    sa.or_(
                        versions.c.owner == tasks.c.owner,
                        sa.and_(
                            versions.c.owner.is_(None),
                            tasks.c.owner.is_(None),
                        ),
                    ),
                )
                .scalar_subquery(),
                1,
            )
        op.execute(
            tasks.update()
            .where(tasks.c.generation.is_(None))
            .values(generation=backfill)
        )
    else:
        logging.warning(
            "Table '%s' does not exist. Skipping upgrade for this table.",
            tasks_table,
        )

    if table_exists(events_table):
        add_column(events_table, 'generation', True, sa.BigInteger())
        events = sa.table(
            events_table,
            sa.column('task_version', sa.BigInteger),
            sa.column('generation', sa.BigInteger),
        )
        op.execute(
            events.update()
            .where(events.c.generation.is_(None))
            .values(generation=events.c.task_version)
        )
    else:
        logging.info(
            "Table '%s' does not exist. Skipping upgrade for this table.",
            events_table,
        )


def downgrade() -> None:
    """Drop the `generation` columns."""
    tasks_table, events_table, _ = _table_names()
    for table in (events_table, tasks_table):
        if table_exists(table):
            drop_column(table, 'generation')
