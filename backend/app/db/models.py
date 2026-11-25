from datetime import datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
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
        CheckConstraint(
            "role IN ('customer', 'admin', 'super_admin')",
            name="ck_users_role_valid",
        ),
        Index("uq_users_email_lower", func.lower(text("email")), unique=True),
        Index("ix_users_role", "role"),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="customer",
        server_default=text("'customer'"),
    )
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
    gemini_settings: Mapped["UserGeminiSettings | None"] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        uselist=False,
    )
    gemini_usage_events: Mapped[list["GeminiUsageEvent"]] = relationship(
        back_populates="user",
    )
    admin_audit_logs: Mapped[list["AdminAuditLog"]] = relationship(
        back_populates="actor",
        foreign_keys="AdminAuditLog.actor_user_id",
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


class UserGeminiSettings(TimestampMixin, Base):
    __tablename__ = "user_gemini_settings"
    __table_args__ = (
        Index("ix_user_gemini_settings_status", "status"),
    )

    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    encrypted_api_key: Mapped[str] = mapped_column(Text, nullable=False)
    key_hint: Mapped[str] = mapped_column(String(32), nullable=False)
    key_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    is_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="ACTIVE", server_default=text("'ACTIVE'")
    )
    last_validated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cooldown_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    user: Mapped[User] = relationship(back_populates="gemini_settings")


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
        CheckConstraint(
            "(idempotency_key IS NULL AND request_fingerprint IS NULL) "
            "OR (idempotency_key IS NOT NULL AND request_fingerprint IS NOT NULL AND length(request_fingerprint) = 64)",
            name="ck_orders_request_fingerprint_valid",
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
    customer_email_snapshot: Mapped[str] = mapped_column(
        String(320), nullable=False, default="", server_default=text("''")
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    request_fingerprint: Mapped[str | None] = mapped_column(String(64))

    user: Mapped[User] = relationship(back_populates="orders")
    shipping_address: Mapped[Address | None] = relationship()
    items: Mapped[list["OrderItem"]] = relationship(
        back_populates="order",
        cascade="all, delete-orphan",
        order_by="OrderItem.created_at",
    )
    verifications: Mapped[list["GuestOrderVerification"]] = relationship(
        back_populates="order",
        cascade="all, delete-orphan",
        order_by="GuestOrderVerification.created_at.desc()",
    )
    guest_sessions: Mapped[list["GuestAccessSession"]] = relationship(
        back_populates="order",
        cascade="all, delete-orphan",
        order_by="GuestAccessSession.created_at.desc()",
    )
    outbox_messages: Mapped[list["GuestEmailOutbox"]] = relationship(
        back_populates="order",
        cascade="all, delete-orphan",
        order_by="GuestEmailOutbox.created_at.asc()",
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


class GuestOrderVerification(Base):
    __tablename__ = "guest_order_verifications"
    __table_args__ = (
        CheckConstraint(
            "attempt_count >= 0",
            name="ck_guest_verifications_attempt_count_nonnegative",
        ),
        CheckConstraint(
            "resend_count >= 0",
            name="ck_guest_verifications_resend_count_nonnegative",
        ),
        Index("ix_guest_verifications_order_created", "order_id", "created_at"),
        Index("ix_guest_verifications_email_created", "email_snapshot", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    order_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
    )
    email_snapshot: Mapped[str] = mapped_column(String(320), nullable=False)
    otp_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    resend_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    delivery_failed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    order: Mapped[Order] = relationship(back_populates="verifications")
    outbox_messages: Mapped[list["GuestEmailOutbox"]] = relationship(
        back_populates="verification",
        cascade="all, delete-orphan",
    )


class GuestAccessSession(Base):
    __tablename__ = "guest_access_sessions"
    __table_args__ = (
        Index("uq_guest_access_sessions_jti_hash", "token_jti_hash", unique=True),
        Index("ix_guest_access_sessions_order_expires", "order_id", "expires_at"),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    order_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
    )
    token_jti_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    order: Mapped[Order] = relationship(back_populates="guest_sessions")


class OutboxDeliveryStatus(str, Enum):
    PENDING = "pending"
    RETRY = "retry"
    SENT = "sent"
    FAILED = "failed"


class GuestEmailOutbox(Base):
    __tablename__ = "guest_email_outbox"
    __table_args__ = (
        CheckConstraint(
            "attempts >= 0",
            name="ck_guest_email_outbox_attempts_nonnegative",
        ),
        CheckConstraint(
            "max_attempts > 0",
            name="ck_guest_email_outbox_max_attempts_positive",
        ),
        CheckConstraint(
            "status IN ('pending', 'retry', 'sent', 'failed')",
            name="ck_guest_email_outbox_status_valid",
        ),
        Index("ix_guest_email_outbox_status_retry", "status", "next_retry_at"),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    order_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    verification_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("guest_order_verifications.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    recipient_email: Mapped[str] = mapped_column(String(320), nullable=False)
    order_number: Mapped[str] = mapped_column(String(64), nullable=False)
    encrypted_otp_payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=OutboxDeliveryStatus.PENDING.value,
        server_default=text("'pending'"),
    )
    attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=3,
        server_default=text("3"),
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_retry_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    lease_token: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), nullable=True
    )
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
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

    order: Mapped[Order] = relationship(back_populates="outbox_messages")
    verification: Mapped[GuestOrderVerification] = relationship(back_populates="outbox_messages")


class AuthRateLimit(Base):
    """Distributed storage for authentication rate limiting across multiple workers."""
    __tablename__ = "auth_rate_limits"

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )


class GeminiUsageEvent(Base):
    """Durable telemetry log for Gemini API usage, tokens, and failovers."""
    __tablename__ = "gemini_usage_events"
    __table_args__ = (
        Index("ix_gemini_usage_events_user_created", "user_id", "created_at"),
        Index("ix_gemini_usage_events_source_created", "key_source", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    user_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="google")
    model_name: Mapped[str] = mapped_column(String(64), nullable=False)
    key_source: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    key_slot: Mapped[str | None] = mapped_column(String(64), nullable=True)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fallback_reason: Mapped[str | None] = mapped_column(String(128), nullable=True)
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        index=True,
    )

    user: Mapped[User | None] = relationship(back_populates="gemini_usage_events")


class AdminAuditLog(Base):
    """Immutable audit trail for sensitive administrative and security actions."""
    __tablename__ = "admin_audit_logs"
    __table_args__ = (
        Index("ix_admin_audit_logs_actor_created", "actor_user_id", "created_at"),
        Index("ix_admin_audit_logs_action_created", "action", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    actor_user_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    target_type: Mapped[str] = mapped_column(String(64), nullable=False)
    target_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        index=True,
    )

    actor: Mapped[User | None] = relationship(
        back_populates="admin_audit_logs",
        foreign_keys=[actor_user_id],
    )


class ProviderConnectionStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    PLANNED = "PLANNED"
    CONFIGURED = "CONFIGURED"
    DEGRADED = "DEGRADED"
    ERROR = "ERROR"


class ModelAccessClass(str, Enum):
    FREE_TIER = "FREE_TIER"
    PROMOTIONAL_CREDITS = "PROMOTIONAL_CREDITS"
    PAY_AS_YOU_GO = "PAY_AS_YOU_GO"
    SELF_HOSTED = "SELF_HOSTED"
    UNKNOWN = "UNKNOWN"


class PlatformKeyStatus(str, Enum):
    UNVERIFIED = "UNVERIFIED"
    ACTIVE = "ACTIVE"
    COOLDOWN = "COOLDOWN"
    QUOTA_EXHAUSTED = "QUOTA_EXHAUSTED"
    INVALID = "INVALID"
    REVOKED = "REVOKED"


class AIProvider(TimestampMixin, Base):
    """Registry entry for an external LLM / AI service provider."""
    __tablename__ = "ai_providers"
    __table_args__ = (
        Index("uq_ai_providers_code_lower", func.lower(text("code")), unique=True),
        Index("ix_ai_providers_status_enabled", "connection_status", "is_enabled"),
        CheckConstraint(
            "connection_status IN ('AVAILABLE', 'PLANNED', 'CONFIGURED', 'DEGRADED', 'ERROR')",
            name="ck_ai_providers_status_valid",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    adapter_type: Mapped[str] = mapped_column(String(64), nullable=False, default="unsupported")
    base_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    documentation_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    is_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    priority: Mapped[int] = mapped_column(
        Integer, nullable=False, default=100, server_default=text("100")
    )
    connection_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=ProviderConnectionStatus.PLANNED.value,
        server_default=text("'PLANNED'"),
    )

    models: Mapped[list["AIModel"]] = relationship(
        back_populates="provider",
        cascade="all, delete-orphan",
        order_by="AIModel.display_name.asc()",
    )
    keys: Mapped[list["PlatformAIKey"]] = relationship(
        back_populates="provider",
        cascade="all, delete-orphan",
        order_by="PlatformAIKey.created_at.desc()",
    )


class AIModel(TimestampMixin, Base):
    """Specific model provided by an AIProvider with capabilities, context, and pricing metadata."""
    __tablename__ = "ai_models"
    __table_args__ = (
        UniqueConstraint("provider_id", "model_identifier", name="uq_ai_models_provider_identifier"),
        Index("ix_ai_models_provider_enabled", "provider_id", "is_enabled"),
        Index("ix_ai_models_access_class", "access_class"),
        CheckConstraint(
            "access_class IN ('FREE_TIER', 'PROMOTIONAL_CREDITS', 'PAY_AS_YOU_GO', 'SELF_HOSTED', 'UNKNOWN')",
            name="ck_ai_models_access_class_valid",
        ),
        CheckConstraint(
            "context_window IS NULL OR context_window > 0",
            name="ck_ai_models_context_window_positive",
        ),
        CheckConstraint(
            "max_output_tokens IS NULL OR max_output_tokens > 0",
            name="ck_ai_models_max_output_tokens_positive",
        ),
        CheckConstraint(
            "pricing_input_per_million IS NULL OR pricing_input_per_million >= 0",
            name="ck_ai_models_pricing_input_nonnegative",
        ),
        CheckConstraint(
            "pricing_output_per_million IS NULL OR pricing_output_per_million >= 0",
            name="ck_ai_models_pricing_output_nonnegative",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    provider_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("ai_providers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    model_identifier: Mapped[str] = mapped_column(String(128), nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Capability flags
    supports_text: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    supports_vision: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    supports_tools: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    supports_streaming: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    supports_json_mode: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    supports_embeddings: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    supports_speech_input: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    supports_speech_output: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )

    # Limits
    context_window: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Pricing per million tokens (nullable when unverified)
    pricing_input_per_million: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    pricing_output_per_million: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    pricing_currency: Mapped[str] = mapped_column(
        String(3), nullable=False, default="USD", server_default=text("'USD'")
    )

    # Access classification & metadata
    access_class: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=ModelAccessClass.UNKNOWN.value,
        server_default=text("'UNKNOWN'"),
    )
    free_tier_limit_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    terms_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    pricing_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    last_pricing_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    is_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    is_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )

    provider: Mapped[AIProvider] = relationship(back_populates="models")


class PlatformAIKey(TimestampMixin, Base):
    """Company-owned API credential for an AIProvider with quota tracking and cooldown."""
    __tablename__ = "platform_ai_keys"
    __table_args__ = (
        Index("ix_platform_ai_keys_provider_status", "provider_id", "status"),
        Index("ix_platform_ai_keys_quota_group", "quota_group"),
        CheckConstraint(
            "status IN ('UNVERIFIED', 'ACTIVE', 'COOLDOWN', 'QUOTA_EXHAUSTED', 'INVALID', 'REVOKED')",
            name="ck_platform_ai_keys_status_valid",
        ),
        CheckConstraint("length(quota_group) > 0", name="ck_platform_ai_keys_quota_group_nonempty"),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    provider_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("ai_providers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    encrypted_api_key: Mapped[str] = mapped_column(Text, nullable=False)
    key_hint: Mapped[str] = mapped_column(String(32), nullable=False)
    quota_group: Mapped[str] = mapped_column(String(64), nullable=False)
    key_label: Mapped[str] = mapped_column(String(100), nullable=False)
    is_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=PlatformKeyStatus.UNVERIFIED.value,
        server_default=text("'UNVERIFIED'"),
    )
    cooldown_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_validated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    provider: Mapped[AIProvider] = relationship(back_populates="keys")
