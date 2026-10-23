"""add task_leases table

Revision ID: b0f01b04ff26
Revises: b5e3d1c8a2f7
Create Date: 2026-10-23 16:00:00.000000

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
revision: str = 'b0f01b04ff26'
down_revision: Union[str, Sequence[str], None] = 'b5e3d1c8a2f7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema: create the task_leases table."""
    leases_table = context.config.get_main_option(
        'task_leases_table', 'task_leases'
    )

    if context.is_offline_mode() or not table_exists(leases_table):
        op.create_table(
            leases_table,
            sa.Column('task_id', sa.String(36), primary_key=True),
            sa.Column('owner', sa.String(255), nullable=True),
            sa.Column('token', sa.String(32), nullable=False),
            sa.Column('expires_at_ms', sa.BigInteger(), nullable=False),
        )
        op.create_index(
            f'ix_{leases_table}_expires_at_ms',
            leases_table,
            ['expires_at_ms'],
        )
    else:
        logging.info(
            "Table '%s' already exists. Skipping creation.", leases_table
        )


def downgrade() -> None:
    """Downgrade schema: drop the task_leases table."""
    leases_table = context.config.get_main_option(
        'task_leases_table', 'task_leases'
    )

    if context.is_offline_mode() or table_exists(leases_table):
        op.drop_index(
            f'ix_{leases_table}_expires_at_ms', table_name=leases_table
        )
        op.drop_table(leases_table)
    else:
        logging.info("Table '%s' does not exist. Skipping drop.", leases_table)
