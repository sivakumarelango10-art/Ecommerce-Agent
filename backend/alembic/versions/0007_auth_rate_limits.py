"""Create auth_rate_limits table for distributed authentication rate limiting.

Revision ID: 0007_auth_rate_limits
Revises: 0006_outbox_lease_token
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0007_auth_rate_limits"
down_revision: Union[str, None] = "0006_outbox_lease_token"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "auth_rate_limits",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_auth_rate_limits"),
    )
    op.create_index(
        "ix_auth_rate_limits_key_hash",
        "auth_rate_limits",
        ["key_hash"],
        unique=False,
    )
    op.create_index(
        "ix_auth_rate_limits_action",
        "auth_rate_limits",
        ["action"],
        unique=False,
    )
    op.create_index(
        "ix_auth_rate_limits_created_at",
        "auth_rate_limits",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        "ix_auth_rate_limits_expires_at",
        "auth_rate_limits",
        ["expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_auth_rate_limits_expires_at", table_name="auth_rate_limits")
    op.drop_index("ix_auth_rate_limits_created_at", table_name="auth_rate_limits")
    op.drop_index("ix_auth_rate_limits_action", table_name="auth_rate_limits")
    op.drop_index("ix_auth_rate_limits_key_hash", table_name="auth_rate_limits")
    op.drop_table("auth_rate_limits")
