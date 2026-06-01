"""add user_preferences ntfy notify flags

Revision ID: 7d0e5f3a4b13
Revises: 7d3c1f9b4a08
Create Date: 2026-06-01 12:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7d0e5f3a4b13"
down_revision: str | Sequence[str] | None = "7d3c1f9b4a08"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS = ("notify_manual", "notify_emergency", "notify_alerts", "notify_auto")


def upgrade() -> None:
    with op.batch_alter_table("user_preferences", schema=None) as batch_op:
        for name in _COLUMNS:
            batch_op.add_column(
                sa.Column(
                    name,
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.true(),
                )
            )


def downgrade() -> None:
    with op.batch_alter_table("user_preferences", schema=None) as batch_op:
        for name in reversed(_COLUMNS):
            batch_op.drop_column(name)
