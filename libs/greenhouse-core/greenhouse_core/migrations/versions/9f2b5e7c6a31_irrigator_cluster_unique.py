"""enforce one irrigator per cluster (unique constraint on irrigators.cluster_id)

Revision ID: 9f2b5e7c6a31
Revises: 8e1a4d6c5f20
Create Date: 2026-06-04 00:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "9f2b5e7c6a31"
down_revision: str | Sequence[str] | None = "8e1a4d6c5f20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # A cluster now has at most one irrigator (strict 0:1). Enforce it at the
    # DB level so the invariant holds regardless of which interface writes.
    with op.batch_alter_table("irrigators") as batch_op:
        batch_op.create_unique_constraint("uq_irrigators_cluster_id", ["cluster_id"])


def downgrade() -> None:
    with op.batch_alter_table("irrigators") as batch_op:
        batch_op.drop_constraint("uq_irrigators_cluster_id", type_="unique")
