"""backfill irrigators.type and sensors.type to model_key form

Revision ID: 6c9d4e2f3a12
Revises: 5b8f3c2d4e11
Create Date: 2026-05-15 11:00:00.000000

Companion to the introduction of the device adapter registry. The legacy
``type`` column on ``irrigators`` and ``sensors`` stored transport-/
capability-flavoured strings (``"tuya_cloud"`` / ``"tuya_local"``,
``"soil_moisture"`` / ``"temp_humidity"`` / ``"light"``). The registry now
keys on ``vendor.model``, so we rewrite the values in-place.

Idempotent: the ``WHERE type IN (...)`` clause skips rows that have
already been migrated, so re-running the migration is a no-op.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "6c9d4e2f3a12"
down_revision: str | Sequence[str] | None = "5b8f3c2d4e11"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(sa.text("UPDATE irrigators SET type = 'rainpoint.ik10pw' WHERE type IN ('tuya_cloud', 'tuya_local')"))
    op.execute(
        sa.text("UPDATE sensors SET type = 'tuya.tr301z' WHERE type IN ('soil_moisture', 'temp_humidity', 'light')")
    )


def downgrade() -> None:
    # We cannot recover the original transport/capability flavour from the
    # ``rainpoint.ik10pw`` row, so the downgrade picks a representative
    # value. Both ``tuya_cloud`` and ``soil_moisture`` were the most common
    # rows in deployed installations.
    op.execute(sa.text("UPDATE irrigators SET type = 'tuya_cloud' WHERE type = 'rainpoint.ik10pw'"))
    op.execute(sa.text("UPDATE sensors SET type = 'soil_moisture' WHERE type = 'tuya.tr301z'"))
