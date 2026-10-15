"""soft delete: archived_at on sensors and plants

Revision ID: c4d6f8a0b215
Revises: b3c5e7f9a104
Create Date: 2026-10-15 12:00:00.000000

Deleting a sensor or a plant becomes an archive by default; a hard purge is only
allowed on an archived row (issue #149). NULL = active, so existing rows stay active.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4d6f8a0b215"
down_revision: str | Sequence[str] | None = "b3c5e7f9a104"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("sensors", sa.Column("archived_at", sa.Integer(), nullable=True))
    op.add_column("plants", sa.Column("archived_at", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("plants") as batch_op:
        batch_op.drop_column("archived_at")
    with op.batch_alter_table("sensors") as batch_op:
        batch_op.drop_column("archived_at")
