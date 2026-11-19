from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from backend.app.db.models import Inventory, Product, ProductCategory


def list_products(
    session: Session,
    *,
    limit: int,
    offset: int,
    category_slug: str | None,
) -> list[Product]:
    query = select(Product).where(Product.is_active.is_(True))
    if category_slug:
        query = query.join(Product.categories).where(
            ProductCategory.slug == category_slug.lower()
        )
    query = (
        query.options(selectinload(Product.categories))
        .order_by(Product.name, Product.id)
        .limit(limit)
        .offset(offset)
    )
    return list(session.scalars(query).unique())


def get_product(session: Session, product_id: UUID) -> Product | None:
    return session.scalar(
        select(Product)
        .where(Product.id == product_id, Product.is_active.is_(True))
        .options(selectinload(Product.categories))
    )


def list_categories(session: Session) -> list[ProductCategory]:
    return list(
        session.scalars(
            select(ProductCategory).order_by(ProductCategory.name, ProductCategory.id)
        )
    )


def get_inventory_availability(
    session: Session, product_id: UUID
) -> tuple[bool, int] | None:
    row = session.execute(
        select(Inventory.available_quantity)
        .select_from(Product)
        .outerjoin(Inventory, Inventory.product_id == Product.id)
        .where(Product.id == product_id, Product.is_active.is_(True))
    ).one_or_none()
    if row is None:
        return None
    quantity = row[0] or 0
    return quantity > 0, quantity
