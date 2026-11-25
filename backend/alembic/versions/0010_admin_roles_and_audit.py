"""Add role column to users and create admin_audit_logs table.

Revision ID: 0010_admin_roles_and_audit
Revises: 0009_gemini_usage_events
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0010_admin_roles_and_audit"
down_revision: Union[str, None] = "0009_gemini_usage_events"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add role column to users with default 'customer'
    op.add_column(
        "users",
        sa.Column(
            "role",
            sa.String(length=20),
            server_default=sa.text("'customer'"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_users_role_valid",
        "users",
        "role IN ('customer', 'admin', 'super_admin')",
    )
    op.create_index(
        "ix_users_role",
        "users",
        ["role"],
        unique=False,
    )

    # 2. Create admin_audit_logs table
    op.create_table(
        "admin_audit_logs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("target_type", sa.String(length=64), nullable=False),
        sa.Column("target_id", sa.String(length=128), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name="fk_admin_audit_logs_actor_user_id_users",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_admin_audit_logs"),
    )
    op.create_index(
        "ix_admin_audit_logs_actor_user_id",
        "admin_audit_logs",
        ["actor_user_id"],
        unique=False,
    )
    op.create_index(
        "ix_admin_audit_logs_action",
        "admin_audit_logs",
        ["action"],
        unique=False,
    )
    op.create_index(
        "ix_admin_audit_logs_created_at",
        "admin_audit_logs",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        "ix_admin_audit_logs_actor_created",
        "admin_audit_logs",
        ["actor_user_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_admin_audit_logs_action_created",
        "admin_audit_logs",
        ["action", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    # 1. Drop admin_audit_logs and its indexes
    op.drop_index("ix_admin_audit_logs_action_created", table_name="admin_audit_logs")
    op.drop_index("ix_admin_audit_logs_actor_created", table_name="admin_audit_logs")
    op.drop_index("ix_admin_audit_logs_created_at", table_name="admin_audit_logs")
    op.drop_index("ix_admin_audit_logs_action", table_name="admin_audit_logs")
    op.drop_index("ix_admin_audit_logs_actor_user_id", table_name="admin_audit_logs")
    op.drop_table("admin_audit_logs")

    # 2. Drop users role index, check constraint, and column
    op.drop_index("ix_users_role", table_name="users")
    op.drop_constraint("ck_users_role_valid", "users", type_="check")
    op.drop_column("users", "role")
