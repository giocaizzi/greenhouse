"""per-plant soil_moisture_target override

Revision ID: d5e7a9b1c326
Revises: c4d6f8a0b215
Create Date: 2026-10-15 18:00:00.000000

A plant may carry its own ``"min-max"`` soil band, used ahead of the plant
database (issue #182). NULL = no override, so existing plants keep their band.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d5e7a9b1c326"
down_revision: str | Sequence[str] | None = "c4d6f8a0b215"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("plants", sa.Column("soil_moisture_target", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("plants") as batch_op:
        batch_op.drop_column("soil_moisture_target")
