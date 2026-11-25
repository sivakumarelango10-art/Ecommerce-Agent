"""Create multi-provider AI registry, model catalogue, and platform key vault tables.

Revision ID: 0011_multi_provider_registry
Revises: 0010_admin_roles_and_audit
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0011_multi_provider_registry"
down_revision: Union[str, None] = "0010_admin_roles_and_audit"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create ai_providers table
    op.create_table(
        "ai_providers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=128), nullable=False),
        sa.Column("adapter_type", sa.String(length=64), server_default=sa.text("'unsupported'"), nullable=False),
        sa.Column("base_url", sa.String(length=512), nullable=True),
        sa.Column("documentation_url", sa.String(length=512), nullable=True),
        sa.Column("is_enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("priority", sa.Integer(), server_default=sa.text("100"), nullable=False),
        sa.Column("connection_status", sa.String(length=32), server_default=sa.text("'PLANNED'"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_ai_providers_code"),
        sa.CheckConstraint(
            "connection_status IN ('AVAILABLE', 'PLANNED', 'CONFIGURED', 'DEGRADED', 'ERROR')",
            name="ck_ai_providers_status_valid",
        ),
    )
    op.create_index("ix_ai_providers_status_enabled", "ai_providers", ["connection_status", "is_enabled"], unique=False)
    op.create_index("uq_ai_providers_code_lower", "ai_providers", [sa.text("lower(code)")], unique=True)

    # 2. Create ai_models table
    op.create_table(
        "ai_models",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("provider_id", sa.Uuid(), nullable=False),
        sa.Column("model_identifier", sa.String(length=128), nullable=False),
        sa.Column("display_name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("supports_text", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("supports_vision", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("supports_tools", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("supports_streaming", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("supports_json_mode", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("supports_embeddings", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("supports_speech_input", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("supports_speech_output", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("context_window", sa.Integer(), nullable=True),
        sa.Column("max_output_tokens", sa.Integer(), nullable=True),
        sa.Column("pricing_input_per_million", sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column("pricing_output_per_million", sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column("pricing_currency", sa.String(length=3), server_default=sa.text("'USD'"), nullable=False),
        sa.Column("access_class", sa.String(length=32), server_default=sa.text("'UNKNOWN'"), nullable=False),
        sa.Column("free_tier_limit_metadata", sa.JSON(), nullable=True),
        sa.Column("terms_url", sa.String(length=512), nullable=True),
        sa.Column("pricing_url", sa.String(length=512), nullable=True),
        sa.Column("last_pricing_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("is_default", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["provider_id"], ["ai_providers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider_id", "model_identifier", name="uq_ai_models_provider_identifier"),
        sa.CheckConstraint(
            "access_class IN ('FREE_TIER', 'PROMOTIONAL_CREDITS', 'PAY_AS_YOU_GO', 'SELF_HOSTED', 'UNKNOWN')",
            name="ck_ai_models_access_class_valid",
        ),
        sa.CheckConstraint(
            "context_window IS NULL OR context_window > 0",
            name="ck_ai_models_context_window_positive",
        ),
        sa.CheckConstraint(
            "max_output_tokens IS NULL OR max_output_tokens > 0",
            name="ck_ai_models_max_output_tokens_positive",
        ),
        sa.CheckConstraint(
            "pricing_input_per_million IS NULL OR pricing_input_per_million >= 0",
            name="ck_ai_models_pricing_input_nonnegative",
        ),
        sa.CheckConstraint(
            "pricing_output_per_million IS NULL OR pricing_output_per_million >= 0",
            name="ck_ai_models_pricing_output_nonnegative",
        ),
    )
    op.create_index("ix_ai_models_provider_id", "ai_models", ["provider_id"], unique=False)
    op.create_index("ix_ai_models_provider_enabled", "ai_models", ["provider_id", "is_enabled"], unique=False)
    op.create_index("ix_ai_models_access_class", "ai_models", ["access_class"], unique=False)

    # 3. Create platform_ai_keys table
    op.create_table(
        "platform_ai_keys",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("provider_id", sa.Uuid(), nullable=False),
        sa.Column("encrypted_api_key", sa.Text(), nullable=False),
        sa.Column("key_hint", sa.String(length=32), nullable=False),
        sa.Column("quota_group", sa.String(length=64), nullable=False),
        sa.Column("key_label", sa.String(length=100), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("status", sa.String(length=32), server_default=sa.text("'UNVERIFIED'"), nullable=False),
        sa.Column("cooldown_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["provider_id"], ["ai_providers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "status IN ('UNVERIFIED', 'ACTIVE', 'COOLDOWN', 'QUOTA_EXHAUSTED', 'INVALID', 'REVOKED')",
            name="ck_platform_ai_keys_status_valid",
        ),
        sa.CheckConstraint("length(quota_group) > 0", name="ck_platform_ai_keys_quota_group_nonempty"),
    )
    op.create_index("ix_platform_ai_keys_provider_id", "platform_ai_keys", ["provider_id"], unique=False)
    op.create_index("ix_platform_ai_keys_provider_status", "platform_ai_keys", ["provider_id", "status"], unique=False)
    op.create_index("ix_platform_ai_keys_quota_group", "platform_ai_keys", ["quota_group"], unique=False)


def downgrade() -> None:
    op.drop_table("platform_ai_keys")
    op.drop_table("ai_models")
    op.drop_table("ai_providers")
