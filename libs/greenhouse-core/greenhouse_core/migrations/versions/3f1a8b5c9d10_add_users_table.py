"""add users table

Revision ID: 3f1a8b5c9d10
Revises: 2a4d1e7c8f02
Create Date: 2026-05-15 03:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "3f1a8b5c9d10"
down_revision: str | Sequence[str] | None = "2a4d1e7c8f02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(), nullable=False),
        sa.Column("hashed_password", sa.String(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("last_login_at", sa.Integer(), nullable=True),
        sa.UniqueConstraint("username", name="uq_users_username"),
    )


def downgrade() -> None:
    op.drop_table("users")
