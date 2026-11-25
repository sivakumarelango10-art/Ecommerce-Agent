"""Create gemini_usage_events table for AI request and telemetry tracking.

Revision ID: 0009_gemini_usage_events
Revises: 0008_user_gemini_settings
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0009_gemini_usage_events"
down_revision: Union[str, None] = "0008_user_gemini_settings"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "gemini_usage_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("correlation_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("provider", sa.String(length=32), server_default="google", nullable=False),
        sa.Column("model_name", sa.String(length=64), nullable=False),
        sa.Column("key_source", sa.String(length=16), nullable=False),
        sa.Column("key_slot", sa.String(length=64), nullable=True),
        sa.Column("attempt_number", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("error_category", sa.String(length=64), nullable=True),
        sa.Column("fallback_reason", sa.String(length=128), nullable=True),
        sa.Column("latency_ms", sa.Float(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_gemini_usage_events_user_id_users",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_gemini_usage_events"),
    )
    op.create_index(
        "ix_gemini_usage_events_correlation_id",
        "gemini_usage_events",
        ["correlation_id"],
        unique=False,
    )
    op.create_index(
        "ix_gemini_usage_events_user_id",
        "gemini_usage_events",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_gemini_usage_events_key_source",
        "gemini_usage_events",
        ["key_source"],
        unique=False,
    )
    op.create_index(
        "ix_gemini_usage_events_status",
        "gemini_usage_events",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_gemini_usage_events_created_at",
        "gemini_usage_events",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        "ix_gemini_usage_events_user_created",
        "gemini_usage_events",
        ["user_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_gemini_usage_events_source_created",
        "gemini_usage_events",
        ["key_source", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_gemini_usage_events_source_created", table_name="gemini_usage_events")
    op.drop_index("ix_gemini_usage_events_user_created", table_name="gemini_usage_events")
    op.drop_index("ix_gemini_usage_events_created_at", table_name="gemini_usage_events")
    op.drop_index("ix_gemini_usage_events_status", table_name="gemini_usage_events")
    op.drop_index("ix_gemini_usage_events_key_source", table_name="gemini_usage_events")
    op.drop_index("ix_gemini_usage_events_user_id", table_name="gemini_usage_events")
    op.drop_index("ix_gemini_usage_events_correlation_id", table_name="gemini_usage_events")
    op.drop_table("gemini_usage_events")
