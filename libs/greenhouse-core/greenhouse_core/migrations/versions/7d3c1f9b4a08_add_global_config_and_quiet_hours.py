"""add global_irrigation_config + quiet-hours columns

Revision ID: 7d3c1f9b4a08
Revises: 6c9d4e2f3a12
Create Date: 2026-05-19 10:00:00.000000

Two coupled changes that turn irrigation config into a 2-level hierarchy:

* Adds ``global_irrigation_config`` as a single-row defaults table mirroring
  ``irrigation_configs``. All columns are nullable so the resolver can fall
  through to project-wide constants. The migration seeds exactly one row
  with the baseline quiet-hours window (00:00–05:00 local time).
* Adds ``quiet_start_hour`` / ``quiet_end_hour`` to ``irrigation_configs``
  (nullable; ``start == end`` means quiet hours are explicitly disabled at
  the cluster level).
* Relaxes ``irrigation_configs.mode`` and ``irrigation_configs.auto_run``
  to nullable so a null at the cluster level inherits from the global row.

Idempotent for the seed row via ``INSERT OR IGNORE`` on the singleton pk.
"""

from __future__ import annotations

import time
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7d3c1f9b4a08"
down_revision: str | Sequence[str] | None = "6c9d4e2f3a12"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEFAULT_QUIET_START_HOUR = 0
_DEFAULT_QUIET_END_HOUR = 5


def upgrade() -> None:
    op.create_table(
        "global_irrigation_config",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("mode", sa.String(), nullable=True),
        sa.Column("duration_minutes", sa.Integer(), nullable=True),
        sa.Column("interval_hours", sa.Integer(), nullable=True),
        sa.Column("auto_run", sa.Boolean(), nullable=True),
        sa.Column("daily_cap_minutes", sa.Integer(), nullable=True),
        sa.Column("max_events_per_day", sa.Integer(), nullable=True),
        sa.Column("quiet_start_hour", sa.Integer(), nullable=True),
        sa.Column("quiet_end_hour", sa.Integer(), nullable=True),
        sa.Column("last_updated", sa.Integer(), nullable=False),
    )
    op.execute(
        sa.text(
            "INSERT OR IGNORE INTO global_irrigation_config "
            "(id, quiet_start_hour, quiet_end_hour, last_updated) "
            "VALUES (1, :qs, :qe, :ts)"
        ).bindparams(
            qs=_DEFAULT_QUIET_START_HOUR,
            qe=_DEFAULT_QUIET_END_HOUR,
            ts=int(time.time()),
        )
    )

    with op.batch_alter_table("irrigation_configs") as batch_op:
        batch_op.add_column(sa.Column("quiet_start_hour", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("quiet_end_hour", sa.Integer(), nullable=True))
        batch_op.alter_column("mode", existing_type=sa.String(), nullable=True)
        batch_op.alter_column("auto_run", existing_type=sa.Boolean(), nullable=True)


def downgrade() -> None:
    with op.batch_alter_table("irrigation_configs") as batch_op:
        batch_op.alter_column("auto_run", existing_type=sa.Boolean(), nullable=False)
        batch_op.alter_column("mode", existing_type=sa.String(), nullable=False)
        batch_op.drop_column("quiet_end_hour")
        batch_op.drop_column("quiet_start_hour")
    op.drop_table("global_irrigation_config")
