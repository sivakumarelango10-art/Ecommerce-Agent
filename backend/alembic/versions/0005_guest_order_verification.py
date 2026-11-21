"""Add customer_email_snapshot to orders with backfill and create guest verification and session tables.

Revision ID: 0005_guest_order_verification
Revises: 0004_order_fingerprint
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0005_guest_order_verification"
down_revision: Union[str, None] = "0004_order_fingerprint"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add customer_email_snapshot as nullable column using batch_alter_table
    with op.batch_alter_table("orders", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("customer_email_snapshot", sa.String(length=320), nullable=True)
        )

    # 2. Backfill customer_email_snapshot from associated users.email
    # Note: Historical email cannot be reconstructed from existing records;
    # the backfill represents the user's current email at migration time.
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "UPDATE orders SET customer_email_snapshot = ("
            "    SELECT email FROM users WHERE users.id = orders.user_id"
            ") WHERE customer_email_snapshot IS NULL"
        )
    )

    # Verify backfill completeness before altering to non-null
    remaining = bind.execute(
        sa.text("SELECT count(*) FROM orders WHERE customer_email_snapshot IS NULL")
    ).scalar()
    if remaining and remaining > 0:
        raise RuntimeError(
            f"Migration 0005 backfill incomplete: {remaining} orders missing customer_email_snapshot"
        )

    # 3. Make customer_email_snapshot non-null
    with op.batch_alter_table("orders", schema=None) as batch_op:
        batch_op.alter_column(
            "customer_email_snapshot",
            existing_type=sa.String(length=320),
            nullable=False,
        )

    # 4. Create guest_order_verifications table
    op.create_table(
        "guest_order_verifications",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("email_snapshot", sa.String(length=320), nullable=False),
        sa.Column("otp_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "resend_count", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "delivery_failed",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name="ck_guest_verifications_attempt_count_nonnegative",
        ),
        sa.CheckConstraint(
            "resend_count >= 0",
            name="ck_guest_verifications_resend_count_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            name="fk_guest_verifications_order_id_orders",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_guest_order_verifications"),
    )
    op.create_index(
        "ix_guest_verifications_order_created",
        "guest_order_verifications",
        ["order_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_guest_verifications_email_created",
        "guest_order_verifications",
        ["email_snapshot", "created_at"],
        unique=False,
    )

    # 5. Create guest_access_sessions table
    op.create_table(
        "guest_access_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("token_jti_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            name="fk_guest_access_sessions_order_id_orders",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_guest_access_sessions"),
    )
    op.create_index(
        "uq_guest_access_sessions_jti_hash",
        "guest_access_sessions",
        ["token_jti_hash"],
        unique=True,
    )
    op.create_index(
        "ix_guest_access_sessions_order_expires",
        "guest_access_sessions",
        ["order_id", "expires_at"],
        unique=False,
    )

    # 6. Create guest_email_outbox table
    op.create_table(
        "guest_email_outbox",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("verification_id", sa.Uuid(), nullable=False),
        sa.Column("recipient_email", sa.String(length=320), nullable=False),
        sa.Column("order_number", sa.String(length=64), nullable=False),
        sa.Column("encrypted_otp_payload", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column(
            "attempts",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "max_attempts",
            sa.Integer(),
            server_default=sa.text("3"),
            nullable=False,
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "attempts >= 0",
            name="ck_guest_email_outbox_attempts_nonnegative",
        ),
        sa.CheckConstraint(
            "max_attempts > 0",
            name="ck_guest_email_outbox_max_attempts_positive",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'retry', 'sent', 'failed')",
            name="ck_guest_email_outbox_status_valid",
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            name="fk_guest_email_outbox_order_id_orders",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["verification_id"],
            ["guest_order_verifications.id"],
            name="fk_guest_email_outbox_verification_id_guest_order_verifications",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_guest_email_outbox"),
    )
    op.create_index(
        "ix_guest_email_outbox_order_id",
        "guest_email_outbox",
        ["order_id"],
        unique=False,
    )
    op.create_index(
        "ix_guest_email_outbox_verification_id",
        "guest_email_outbox",
        ["verification_id"],
        unique=False,
    )
    op.create_index(
        "ix_guest_email_outbox_status_retry",
        "guest_email_outbox",
        ["status", "next_retry_at"],
        unique=False,
    )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    tables = set(insp.get_table_names())

    if "guest_email_outbox" in tables:
        indexes = {idx["name"] for idx in insp.get_indexes("guest_email_outbox")}
        if "ix_guest_email_outbox_status_retry" in indexes:
            op.drop_index("ix_guest_email_outbox_status_retry", table_name="guest_email_outbox")
        if "ix_guest_email_outbox_verification_id" in indexes:
            op.drop_index("ix_guest_email_outbox_verification_id", table_name="guest_email_outbox")
        if "ix_guest_email_outbox_order_id" in indexes:
            op.drop_index("ix_guest_email_outbox_order_id", table_name="guest_email_outbox")
        op.drop_table("guest_email_outbox")

    if "guest_access_sessions" in tables:
        indexes = {idx["name"] for idx in insp.get_indexes("guest_access_sessions")}
        if "ix_guest_access_sessions_order_expires" in indexes:
            op.drop_index("ix_guest_access_sessions_order_expires", table_name="guest_access_sessions")
        if "uq_guest_access_sessions_jti_hash" in indexes:
            op.drop_index("uq_guest_access_sessions_jti_hash", table_name="guest_access_sessions")
        op.drop_table("guest_access_sessions")

    if "guest_order_verifications" in tables:
        indexes = {idx["name"] for idx in insp.get_indexes("guest_order_verifications")}
        if "ix_guest_verifications_email_created" in indexes:
            op.drop_index("ix_guest_verifications_email_created", table_name="guest_order_verifications")
        if "ix_guest_verifications_order_created" in indexes:
            op.drop_index("ix_guest_verifications_order_created", table_name="guest_order_verifications")
        op.drop_table("guest_order_verifications")

    if "orders" in tables:
        cols = {c["name"] for c in insp.get_columns("orders")}
        if "customer_email_snapshot" in cols:
            with op.batch_alter_table("orders", schema=None) as batch_op:
                batch_op.drop_column("customer_email_snapshot")
