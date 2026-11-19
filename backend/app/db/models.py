from datetime import datetime
from decimal import Decimal
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
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

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
