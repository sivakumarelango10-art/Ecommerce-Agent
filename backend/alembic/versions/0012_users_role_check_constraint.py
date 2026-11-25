"""Enforce role check constraint on users for SQLite test fidelity.

Revision ID: 0012_users_role_check
Revises: 0011_multi_provider_registry
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0012_users_role_check"
down_revision: Union[str, None] = "0011_multi_provider_registry"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    # On PostgreSQL, ck_users_role_valid was created in migration 0010.
    # On SQLite, ALTER TABLE ADD CONSTRAINT is not supported, so 0010 could not attach it directly.
    # We apply it here via batch_alter_table on SQLite so both engines enforce identical constraints.
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("users", schema=None) as batch_op:
            batch_op.create_check_constraint(
                "ck_users_role_valid",
                "role IN ('customer', 'admin', 'super_admin')",
            )
        # SQLite batch table copy drops expression-based indexes since they cannot be reflected.
        # Re-create uq_users_email_lower to maintain schema parity.
        op.execute(
            sa.text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_users_email_lower "
                "ON users (lower(email))"
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("users", schema=None) as batch_op:
            batch_op.drop_constraint("ck_users_role_valid", type_="check")
        op.execute(
            sa.text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_users_email_lower "
                "ON users (lower(email))"
            )
        )
