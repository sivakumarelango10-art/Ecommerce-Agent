"""Add request_fingerprint column to orders with safe Python SHA-256 backfill and batch alter.

Revision ID: 0004_order_fingerprint
Revises: 0003_cart_orders
"""
import hashlib
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0004_order_fingerprint"
down_revision: Union[str, None] = "0003_cart_orders"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add column as nullable String(64) using batch_alter_table for SQLite compatibility
    with op.batch_alter_table("orders", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("request_fingerprint", sa.String(length=64), nullable=True)
        )

    # 2. Deterministic SHA-256 backfill in Python across both DB engines (no pgcrypto required)
    bind = op.get_bind()
    orders_table = sa.table(
        "orders",
        sa.column("id", sa.Uuid()),
        sa.column("idempotency_key", sa.String()),
        sa.column("request_fingerprint", sa.String()),
    )

    rows = bind.execute(
        sa.select(orders_table.c.id, orders_table.c.idempotency_key).where(
            orders_table.c.idempotency_key.isnot(None)
        )
    ).fetchall()

    for order_id, key in rows:
        legacy_data = f"legacy_backfill:{str(order_id)}:{key}".encode("utf-8")
        legacy_hash = hashlib.sha256(legacy_data).hexdigest()
        bind.execute(
            orders_table.update()
            .where(orders_table.c.id == order_id)
            .values(request_fingerprint=legacy_hash)
        )

    # 3. Add check constraint enforcing (key NULL and fp NULL) OR (key NOT NULL and fp NOT NULL and length(fp) = 64)
    with op.batch_alter_table("orders", schema=None) as batch_op:
        batch_op.create_check_constraint(
            "ck_orders_request_fingerprint_valid",
            "(idempotency_key IS NULL AND request_fingerprint IS NULL) "
            "OR (idempotency_key IS NOT NULL AND request_fingerprint IS NOT NULL AND length(request_fingerprint) = 64)",
        )


def downgrade() -> None:
    with op.batch_alter_table("orders", schema=None) as batch_op:
        batch_op.drop_constraint("ck_orders_request_fingerprint_valid", type_="check")
        batch_op.drop_column("request_fingerprint")
