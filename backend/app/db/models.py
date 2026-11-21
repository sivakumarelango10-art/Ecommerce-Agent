from datetime import datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Table,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.core.constants import DEFAULT_CATALOGUE_CURRENCY
from backend.app.db.base import Base


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


product_categories = Table(
    "product_category_links",
    Base.metadata,
    Column(
        "product_id",
        Uuid(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "category_id",
        Uuid(as_uuid=True),
        ForeignKey("product_categories.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


class User(TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        Index("uq_users_email_lower", func.lower(text("email")), unique=True),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )

    profile: Mapped["CustomerProfile"] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        uselist=False,
    )
    addresses: Mapped[list["Address"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        order_by="Address.created_at",
    )
    cart: Mapped["Cart | None"] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        uselist=False,
    )
    orders: Mapped[list["Order"]] = relationship(
        back_populates="user",
        order_by="Order.created_at.desc()",
    )


class CustomerProfile(TimestampMixin, Base):
    __tablename__ = "customer_profiles"

    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(32))

    user: Mapped[User] = relationship(back_populates="profile")


class ProductCategory(TimestampMixin, Base):
    __tablename__ = "product_categories"
    __table_args__ = (
        Index("uq_product_category_slug_lower", func.lower(text("slug")), unique=True),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    slug: Mapped[str] = mapped_column(String(120), nullable=False)

    products: Mapped[list["Product"]] = relationship(
        secondary=product_categories,
        back_populates="categories",
    )


class Product(TimestampMixin, Base):
    __tablename__ = "products"
    __table_args__ = (
        CheckConstraint("price >= 0", name="price_nonnegative"),
        Index("uq_products_sku_lower", func.lower(text("sku")), unique=True),
        Index("ix_products_active_name", "is_active", "name"),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    sku: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )

    categories: Mapped[list[ProductCategory]] = relationship(
        secondary=product_categories,
        back_populates="products",
    )
    inventory: Mapped["Inventory | None"] = relationship(
        back_populates="product",
        cascade="all, delete-orphan",
        uselist=False,
    )


class Inventory(TimestampMixin, Base):
    __tablename__ = "inventory"
    __table_args__ = (
        CheckConstraint(
            "available_quantity >= 0",
            name="available_quantity_nonnegative",
        ),
    )

    product_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        primary_key=True,
    )
    available_quantity: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )

    product: Mapped[Product] = relationship(back_populates="inventory")


class Address(TimestampMixin, Base):
    __tablename__ = "addresses"
    __table_args__ = (
        CheckConstraint("length(country_code) = 2", name="country_code_length"),
        Index("ix_addresses_user_id", "user_id"),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    label: Mapped[str] = mapped_column(String(40), nullable=False, default="home")
    recipient_name: Mapped[str] = mapped_column(String(120), nullable=False)
    line1: Mapped[str] = mapped_column(String(200), nullable=False)
    line2: Mapped[str | None] = mapped_column(String(200))
    city: Mapped[str] = mapped_column(String(100), nullable=False)
    region: Mapped[str] = mapped_column(String(100), nullable=False)
    postal_code: Mapped[str] = mapped_column(String(24), nullable=False)
    country_code: Mapped[str] = mapped_column(
        String(2), nullable=False, default="US", server_default=text("'US'")
    )
    is_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )

    user: Mapped[User] = relationship(back_populates="addresses")


class OrderStatus(str, Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    PROCESSING = "processing"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"


class Cart(TimestampMixin, Base):
    __tablename__ = "carts"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_carts_user_id"),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    user: Mapped[User] = relationship(back_populates="cart")
    items: Mapped[list["CartItem"]] = relationship(
        back_populates="cart",
        cascade="all, delete-orphan",
        order_by="CartItem.created_at",
    )


class CartItem(TimestampMixin, Base):
    __tablename__ = "cart_items"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="quantity_positive"),
        UniqueConstraint("cart_id", "product_id", name="uq_cart_items_cart_product"),
        Index("ix_cart_items_product_id", "product_id"),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    cart_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("carts.id", ondelete="CASCADE"),
        nullable=False,
    )
    product_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
    )
    quantity: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )

    cart: Mapped[Cart] = relationship(back_populates="items")
    product: Mapped[Product] = relationship()


class Order(TimestampMixin, Base):
    __tablename__ = "orders"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'confirmed', 'processing', 'shipped', 'delivered', 'cancelled')",
            name="status_valid",
        ),
        CheckConstraint("length(currency) = 3", name="currency_length"),
        CheckConstraint("subtotal >= 0", name="subtotal_nonnegative"),
        CheckConstraint("shipping_fee >= 0", name="shipping_fee_nonnegative"),
        CheckConstraint("tax >= 0", name="tax_nonnegative"),
        CheckConstraint("total_amount >= 0", name="total_amount_nonnegative"),
        CheckConstraint(
            "total_amount = subtotal + shipping_fee + tax",
            name="ck_orders_total_amount_sum",
        ),
        CheckConstraint(
            "length(shipping_country_code) = 2",
            name="shipping_country_code_length",
        ),
        Index("ix_orders_order_number", "order_number", unique=True),
        Index("ix_orders_user_created", "user_id", "created_at"),
        UniqueConstraint("user_id", "idempotency_key", name="uq_orders_user_idempotency"),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    order_number: Mapped[str] = mapped_column(String(32), nullable=False)
    # Historical order preservation: user deletion is RESTRICTed to prevent
    # destroying historical accounting and order records. Customer account closure
    # or deactivation is intended to be handled via soft deletion (User.is_active = False)
    # and profile anonymization.
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    # Shipping address reference: SET NULL on delete to allow customers to remove or
    # update address book entries without affecting historical orders. The immutable
    # snapshot columns below preserve the delivery location as of order placement.
    shipping_address_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("addresses.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Immutable shipping address snapshot
    shipping_recipient_name: Mapped[str] = mapped_column(String(120), nullable=False)
    shipping_line1: Mapped[str] = mapped_column(String(200), nullable=False)
    shipping_line2: Mapped[str | None] = mapped_column(String(200))
    shipping_city: Mapped[str] = mapped_column(String(100), nullable=False)
    shipping_region: Mapped[str] = mapped_column(String(100), nullable=False)
    shipping_postal_code: Mapped[str] = mapped_column(String(24), nullable=False)
    shipping_country_code: Mapped[str] = mapped_column(
        String(2), nullable=False, default="IN", server_default=text("'IN'")
    )

    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=OrderStatus.PENDING.value,
        server_default=text("'pending'"),
    )
    currency: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
        default=DEFAULT_CATALOGUE_CURRENCY,
        server_default=text(f"'{DEFAULT_CATALOGUE_CURRENCY}'"),
    )
    subtotal: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    shipping_fee: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=Decimal("0.00"), server_default=text("0.00")
    )
    tax: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=Decimal("0.00"), server_default=text("0.00")
    )
    total_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(128))

    user: Mapped[User] = relationship(back_populates="orders")
    shipping_address: Mapped[Address | None] = relationship()
    items: Mapped[list["OrderItem"]] = relationship(
        back_populates="order",
        cascade="all, delete-orphan",
        order_by="OrderItem.created_at",
    )


class OrderItem(TimestampMixin, Base):
    __tablename__ = "order_items"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("unit_price >= 0", name="unit_price_nonnegative"),
        CheckConstraint("line_total >= 0", name="line_total_nonnegative"),
        CheckConstraint(
            "line_total = unit_price * quantity",
            name="ck_order_items_line_total_product",
        ),
        Index("ix_order_items_order_id", "order_id"),
        Index("ix_order_items_product_id", "product_id"),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    order_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
    )
    product_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("products.id", ondelete="RESTRICT"),
        nullable=True,
    )
    product_sku: Mapped[str] = mapped_column(String(64), nullable=False)
    product_name: Mapped[str] = mapped_column(String(200), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    line_total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)

    order: Mapped[Order] = relationship(back_populates="items")
    product: Mapped[Product | None] = relationship()
