"""add alerts.last_notified_at (paces the escalating re-notify)

Revision ID: b2e4a6c8d013
Revises: a1d3f5b7c902
Create Date: 2026-10-15 00:00:00.000000

Nullable: an alert opened before this revision has never been timed, so its next
due push falls back to ``first_seen_at``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b2e4a6c8d013"
down_revision: str | Sequence[str] | None = "a1d3f5b7c902"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("alerts") as batch_op:
        batch_op.add_column(sa.Column("last_notified_at", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("alerts") as batch_op:
        batch_op.drop_column("last_notified_at")
