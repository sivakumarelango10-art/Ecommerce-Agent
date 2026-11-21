"""Create cart, cart_items, orders, and order_items tables."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0003_cart_orders"
down_revision: Union[str, None] = "0002_constraint_names"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. carts table
    op.create_table(
        "carts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name="pk_carts"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_carts_user_id_users",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("user_id", name="uq_carts_user_id"),
    )

    # 2. cart_items table
    op.create_table(
        "cart_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("cart_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("quantity", sa.Integer(), server_default=sa.text("1"), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name="pk_cart_items"),
        sa.ForeignKeyConstraint(
            ["cart_id"],
            ["carts.id"],
            name="fk_cart_items_cart_id_carts",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name="fk_cart_items_product_id_products",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("quantity > 0", name="ck_cart_items_quantity_positive"),
        sa.UniqueConstraint("cart_id", "product_id", name="uq_cart_items_cart_product"),
    )
    op.create_index("ix_cart_items_product_id", "cart_items", ["product_id"])

    # 3. orders table
    op.create_table(
        "orders",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("order_number", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("shipping_address_id", sa.Uuid(), nullable=True),
        sa.Column("shipping_recipient_name", sa.String(length=120), nullable=False),
        sa.Column("shipping_line1", sa.String(length=200), nullable=False),
        sa.Column("shipping_line2", sa.String(length=200), nullable=True),
        sa.Column("shipping_city", sa.String(length=100), nullable=False),
        sa.Column("shipping_region", sa.String(length=100), nullable=False),
        sa.Column("shipping_postal_code", sa.String(length=24), nullable=False),
        sa.Column(
            "shipping_country_code",
            sa.String(length=2),
            server_default=sa.text("'IN'"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column(
            "currency",
            sa.String(length=3),
            server_default=sa.text("'INR'"),
            nullable=False,
        ),
        sa.Column("subtotal", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column(
            "shipping_fee",
            sa.Numeric(precision=12, scale=2),
            server_default=sa.text("0.00"),
            nullable=False,
        ),
        sa.Column(
            "tax",
            sa.Numeric(precision=12, scale=2),
            server_default=sa.text("0.00"),
            nullable=False,
        ),
        sa.Column("total_amount", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name="pk_orders"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_orders_user_id_users",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["shipping_address_id"],
            ["addresses.id"],
            name="fk_orders_shipping_address_id_addresses",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'confirmed', 'processing', 'shipped', 'delivered', 'cancelled')",
            name="ck_orders_status_valid",
        ),
        sa.CheckConstraint("length(currency) = 3", name="ck_orders_currency_length"),
        sa.CheckConstraint("subtotal >= 0", name="ck_orders_subtotal_nonnegative"),
        sa.CheckConstraint("shipping_fee >= 0", name="ck_orders_shipping_fee_nonnegative"),
        sa.CheckConstraint("tax >= 0", name="ck_orders_tax_nonnegative"),
        sa.CheckConstraint("total_amount >= 0", name="ck_orders_total_amount_nonnegative"),
        sa.CheckConstraint(
            "total_amount = subtotal + shipping_fee + tax",
            name="ck_orders_total_amount_sum",
        ),
        sa.CheckConstraint(
            "length(shipping_country_code) = 2",
            name="ck_orders_shipping_country_code_length",
        ),
        sa.UniqueConstraint("user_id", "idempotency_key", name="uq_orders_user_idempotency"),
    )
    op.create_index("ix_orders_order_number", "orders", ["order_number"], unique=True)
    op.create_index("ix_orders_user_created", "orders", ["user_id", "created_at"])

    # 4. order_items table
    op.create_table(
        "order_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=True),
        sa.Column("product_sku", sa.String(length=64), nullable=False),
        sa.Column("product_name", sa.String(length=200), nullable=False),
        sa.Column("unit_price", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("line_total", sa.Numeric(precision=12, scale=2), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name="pk_order_items"),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            name="fk_order_items_order_id_orders",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name="fk_order_items_product_id_products",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("quantity > 0", name="ck_order_items_quantity_positive"),
        sa.CheckConstraint("unit_price >= 0", name="ck_order_items_unit_price_nonnegative"),
        sa.CheckConstraint("line_total >= 0", name="ck_order_items_line_total_nonnegative"),
        sa.CheckConstraint(
            "line_total = unit_price * quantity",
            name="ck_order_items_line_total_product",
        ),
    )
    op.create_index("ix_order_items_order_id", "order_items", ["order_id"])
    op.create_index("ix_order_items_product_id", "order_items", ["product_id"])


def downgrade() -> None:
    op.drop_table("order_items")
    op.drop_table("orders")
    op.drop_table("cart_items")
    op.drop_table("carts")
