"""add irrigator reservoir capacity (reservoir_l, flow_rate_l_per_min)

Revision ID: 8e1a4d6c5f20
Revises: 7d0e5f3a4b13
Create Date: 2026-06-03 00:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8e1a4d6c5f20"
down_revision: str | Sequence[str] | None = "7d0e5f3a4b13"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Both columns are nullable: an irrigator with no capacity configured is
    # treated as uncapped by the engine (behaves exactly as before).
    with op.batch_alter_table("irrigators") as batch_op:
        batch_op.add_column(sa.Column("reservoir_l", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("flow_rate_l_per_min", sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("irrigators") as batch_op:
        batch_op.drop_column("flow_rate_l_per_min")
        batch_op.drop_column("reservoir_l")
