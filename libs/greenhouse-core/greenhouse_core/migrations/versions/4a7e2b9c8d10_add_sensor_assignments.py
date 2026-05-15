"""add sensor_assignments table with backfill from current sensor.plant_id

Revision ID: 4a7e2b9c8d10
Revises: 3f1a8b5c9d10
Create Date: 2026-05-15 04:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "4a7e2b9c8d10"
down_revision: str | Sequence[str] | None = "3f1a8b5c9d10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sensor_assignments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("sensor_id", sa.Integer(), nullable=False),
        sa.Column("plant_id", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.Integer(), nullable=False),
        sa.Column("ended_at", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["sensor_id"], ["sensors.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["plant_id"], ["plants.id"], ondelete="CASCADE"),
    )
    op.create_index("idx_sensor_assignments_sensor", "sensor_assignments", ["sensor_id"])
    op.create_index("idx_sensor_assignments_plant", "sensor_assignments", ["plant_id"])
    op.create_index("idx_sensor_assignments_time", "sensor_assignments", ["started_at", "ended_at"])

    # Backfill: for every sensor with a current plant_id, open an assignment
    # starting at epoch 0 so all prior readings stay correctly attributed to
    # the current plant. Subsequent PUTs will close this row and open a new
    # one with the actual reassignment timestamp.
    op.execute(
        """
        INSERT INTO sensor_assignments (sensor_id, plant_id, started_at, ended_at)
        SELECT id, plant_id, 0, NULL FROM sensors WHERE plant_id IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_index("idx_sensor_assignments_time", table_name="sensor_assignments")
    op.drop_index("idx_sensor_assignments_plant", table_name="sensor_assignments")
    op.drop_index("idx_sensor_assignments_sensor", table_name="sensor_assignments")
    op.drop_table("sensor_assignments")
