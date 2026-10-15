"""one open sensor assignment per sensor; record the sensors behind each health snapshot

Revision ID: b3c5e7f9a104
Revises: b2e4a6c8d013
Create Date: 2026-10-15 10:00:00.000000

"One open assignment per sensor" was only a convention; a failed partial write
could leave two open rows, and plant attribution would then count readings
twice. Close any extra open rows first — each one ends where the sensor's next
open row starts, so the newest stays open — then enforce the rule with a partial
unique index. ``plant_health_daily.sensor_ids`` records which sensors fed a
snapshot's score (NULL on older rows).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b3c5e7f9a104"
down_revision: str | Sequence[str] | None = "b2e4a6c8d013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _close_duplicate_open_rows() -> None:
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            "SELECT id, sensor_id, started_at FROM sensor_assignments "
            "WHERE ended_at IS NULL ORDER BY sensor_id, started_at, id"
        )
    ).fetchall()
    for current, following in zip(rows, rows[1:], strict=False):
        if current.sensor_id == following.sensor_id:
            conn.execute(
                sa.text("UPDATE sensor_assignments SET ended_at = :ended WHERE id = :id"),
                {"ended": following.started_at, "id": current.id},
            )


def upgrade() -> None:
    _close_duplicate_open_rows()
    op.create_index(
        "uq_sensor_assignments_open",
        "sensor_assignments",
        ["sensor_id"],
        unique=True,
        sqlite_where=sa.text("ended_at IS NULL"),
    )
    op.add_column("plant_health_daily", sa.Column("sensor_ids", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("plant_health_daily", "sensor_ids")
    op.drop_index("uq_sensor_assignments_open", table_name="sensor_assignments")
