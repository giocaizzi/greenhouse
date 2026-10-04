"""rewrite leftover legacy irrigators.type / sensors.type values to model keys

Revision ID: a1d3f5b7c902
Revises: 9f2b5e7c6a31
Create Date: 2026-10-03 23:30:00.000000

``6c9d4e2f3a12`` already rewrote the legacy transport/capability strings, but rows
created afterwards through the old web forms (which offered only the legacy values)
still carry them. The device registry no longer aliases those values, so an
un-migrated irrigator would be refused at actuation time. Rewrite them in place,
including the empty string the registry used to treat as the default model.

Idempotent: the ``WHERE type IN (...)`` clause skips rows already on a model key.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1d3f5b7c902"
down_revision: str | Sequence[str] | None = "9f2b5e7c6a31"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text("UPDATE irrigators SET type = 'rainpoint.ik10pw' WHERE type IN ('tuya_cloud', 'tuya_local', '')")
    )
    op.execute(
        sa.text("UPDATE sensors SET type = 'tuya.tr301z' WHERE type IN ('soil_moisture', 'temp_humidity', 'light', '')")
    )


def downgrade() -> None:
    # Data-only and lossy: the original legacy flavour is not recoverable, and the
    # model keys are valid at every earlier revision, so there is nothing to undo.
    pass
