"""store task last_updated with microsecond precision on MySQL

Revision ID: d7ef24c86b19
Revises: b5e3d1c8a2f7
Create Date: 2026-11-20 06:37:34.000000

"""

import logging

from collections.abc import Sequence
from typing import Union


try:
    from alembic import context, op
except ImportError as e:
    raise ImportError(
        "A2A migrations require the 'db-cli' extra. Install with: 'pip install a2a-sdk[db-cli]'."
    ) from e

from sqlalchemy.dialects.mysql import DATETIME

from a2a.migrations.migration_utils import table_exists


# revision identifiers, used by Alembic.
revision: str = 'd7ef24c86b19'
down_revision: Union[str, Sequence[str], None] = 'b5e3d1c8a2f7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _set_precision(fsp: int | None) -> None:
    """Alter `last_updated` on MySQL and MariaDB; other backends keep it."""
    if context.get_context().dialect.name not in ('mysql', 'mariadb'):
        return
    tasks_table = context.config.get_main_option('tasks_table', 'tasks')
    if table_exists(tasks_table):
        op.alter_column(
            tasks_table,
            'last_updated',
            type_=DATETIME(fsp=fsp),
            existing_nullable=True,
        )
    else:
        logging.warning(
            "Table '%s' does not exist. Skipping this migration for it.",
            tasks_table,
        )


def upgrade() -> None:
    """Upgrade schema: keep microseconds on new writes."""
    _set_precision(6)


def downgrade() -> None:
    """Downgrade schema: store whole seconds again."""
    _set_precision(None)
