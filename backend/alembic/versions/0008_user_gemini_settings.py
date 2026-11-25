"""Create user_gemini_settings table for encrypted personal Gemini API key storage.

Revision ID: 0008_user_gemini_settings
Revises: 0007_auth_rate_limits
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0008_user_gemini_settings"
down_revision: Union[str, None] = "0007_auth_rate_limits"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_gemini_settings",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("encrypted_api_key", sa.Text(), nullable=False),
        sa.Column("key_hint", sa.String(length=32), nullable=False),
        sa.Column(
            "key_version",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column(
            "is_enabled",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default=sa.text("'ACTIVE'"),
            nullable=False,
        ),
        sa.Column(
            "last_validated_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "last_used_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "cooldown_until",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_user_gemini_settings_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", name="pk_user_gemini_settings"),
    )
    op.create_index(
        "ix_user_gemini_settings_status",
        "user_gemini_settings",
        ["status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_user_gemini_settings_status", table_name="user_gemini_settings")
    op.drop_table("user_gemini_settings")
