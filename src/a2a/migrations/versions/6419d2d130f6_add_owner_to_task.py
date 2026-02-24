"""add_owner_to_task.

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


def upgrade() -> None:
    """Upgrade schema."""
    # Get the default value from the config (passed via CLI)
    owner = context.config.get_main_option('owner', 'unknown')

    op.add_column(
        'tasks',
        sa.Column(
            'owner',
            sa.String(128),
            nullable=False,
            server_default=owner,
        ),
    )

    op.add_column(
        'push_notification_configs',
        sa.Column(
            'owner',
            sa.String(128),
            nullable=False,
            server_default=owner,
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('tasks', 'owner')
    op.drop_column('push_notification_configs', 'owner')
