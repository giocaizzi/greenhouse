"""add irrigation_windows table

Revision ID: 5b8f3c2d4e11
Revises: 4a7e2b9c8d10
Create Date: 2026-05-15 05:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5b8f3c2d4e11"
down_revision: str | Sequence[str] | None = "4a7e2b9c8d10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "irrigation_windows",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("cluster_id", sa.Integer(), nullable=False),
        sa.Column("weekday_mask", sa.Integer(), nullable=False, server_default="127"),
        sa.Column("start_hour", sa.Integer(), nullable=False),
        sa.Column("end_hour", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["cluster_id"], ["clusters.id"], ondelete="CASCADE"),
    )
    op.create_index("idx_irrigation_windows_cluster", "irrigation_windows", ["cluster_id"])


def downgrade() -> None:
    op.drop_index("idx_irrigation_windows_cluster", table_name="irrigation_windows")
    op.drop_table("irrigation_windows")
