"""add task_versions and task_events tables

Revision ID: b5e3d1c8a2f7
Revises: 38ce57e08137
Create Date: 2026-09-14 10:00:00.000000

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

from a2a.migrations.migration_utils import table_exists


# revision identifiers, used by Alembic.
revision: str = 'b5e3d1c8a2f7'
down_revision: Union[str, Sequence[str], None] = '38ce57e08137'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _seq_type() -> sa.types.TypeEngine:
    """Primary-key type for the event log.

    BigInteger everywhere except SQLite, whose AUTOINCREMENT requires a plain
    INTEGER column. Mirrors ``TaskEventMixin.seq`` in a2a.server.models.
    """
    return sa.BigInteger().with_variant(sa.Integer(), 'sqlite')


def upgrade() -> None:
    """Upgrade schema: create the task_versions and task_events tables."""
    versions_table = context.config.get_main_option(
        'task_versions_table', 'task_versions'
    )
    events_table = context.config.get_main_option(
        'task_events_table', 'task_events'
    )

    if context.is_offline_mode() or not table_exists(versions_table):
        op.create_table(
            versions_table,
            sa.Column('task_id', sa.String(36), primary_key=True),
            sa.Column('owner', sa.String(255), nullable=True),
            sa.Column('version', sa.BigInteger(), nullable=False),
        )
    else:
        logging.info(
            "Table '%s' already exists. Skipping creation.", versions_table
        )

    if context.is_offline_mode() or not table_exists(events_table):
        op.create_table(
            events_table,
            sa.Column('seq', _seq_type(), primary_key=True, autoincrement=True),
            sa.Column('task_id', sa.String(36), nullable=False),
            sa.Column('owner', sa.String(255), nullable=True),
            sa.Column('task_version', sa.BigInteger(), nullable=False),
            sa.Column('event_data', sa.LargeBinary(), nullable=False),
        )
        op.create_index(f'ix_{events_table}_task_id', events_table, ['task_id'])
    else:
        logging.info(
            "Table '%s' already exists. Skipping creation.", events_table
        )


def downgrade() -> None:
    """Downgrade schema: drop the task_events and task_versions tables."""
    versions_table = context.config.get_main_option(
        'task_versions_table', 'task_versions'
    )
    events_table = context.config.get_main_option(
        'task_events_table', 'task_events'
    )

    if context.is_offline_mode() or table_exists(events_table):
        op.drop_index(f'ix_{events_table}_task_id', table_name=events_table)
        op.drop_table(events_table)
    else:
        logging.info("Table '%s' does not exist. Skipping drop.", events_table)

    if context.is_offline_mode() or table_exists(versions_table):
        op.drop_table(versions_table)
    else:
        logging.info(
            "Table '%s' does not exist. Skipping drop.", versions_table
        )
