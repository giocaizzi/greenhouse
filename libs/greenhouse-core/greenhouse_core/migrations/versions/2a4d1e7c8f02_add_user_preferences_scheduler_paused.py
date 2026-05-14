"""add user_preferences.scheduler_paused

Revision ID: 2a4d1e7c8f02
Revises: 1c9b09f02432
Create Date: 2026-05-12 10:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2a4d1e7c8f02"
down_revision: str | Sequence[str] | None = "1c9b09f02432"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("user_preferences", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "scheduler_paused",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("user_preferences", schema=None) as batch_op:
        batch_op.drop_column("scheduler_paused")
