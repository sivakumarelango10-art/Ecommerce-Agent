from decimal import Decimal
import logging
from collections.abc import Sequence

from sqlalchemy import and_, distinct, func, or_, select
from sqlalchemy.orm import Session, selectinload

from backend.app.db.models import Inventory, Product, ProductCategory
from backend.app.schemas.shopping import SortOrder

logger = logging.getLogger(__name__)


def get_term_search_variants(term: str) -> set[str]:
    """Generate search variants for a term (handling singular/plural and hyphen/space inflections)."""
    clean = term.strip().lower()
    if not clean:
        return set()
    variants = {clean}
    if "-" in clean:
        variants.add(clean.replace("-", " "))
    if " " in clean:
        variants.add(clean.replace(" ", "-"))

    # Plural to singular normalization
    if clean.endswith("ies") and len(clean) > 4:
        variants.add(clean[:-3] + "y")
    elif clean.endswith("es") and len(clean) > 3 and clean[-3] in {"h", "s", "x", "z"}:
        variants.add(clean[:-2])
    elif clean.endswith("s") and not clean.endswith("ss") and len(clean) > 3:
        variants.add(clean[:-1])
    else:
        # Singular to plural normalization
        if clean.endswith("y") and len(clean) > 2 and clean[-2] not in "aeiou":
            variants.add(clean[:-1] + "ies")
        elif clean.endswith(("ch", "sh", "x", "z", "s")):
            variants.add(clean + "es")
        else:
            variants.add(clean + "s")

    return variants


def search_catalogue_products(
    session: Session,
    *,
    keywords: Sequence[str] | None = None,
    category: str | None = None,
    min_price: Decimal | None = None,
    max_price: Decimal | None = None,
    brand: str | None = None,
    required_features: Sequence[str] | None = None,
    in_stock_only: bool = True,
    min_quantity: int = 1,
    sort_by: SortOrder | str | None = SortOrder.PRICE_ASC,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[Product], int]:
    """Search products deterministically in PostgreSQL with active status and inventory checks.

    Returns:
        (products, total_matching_count)
    """
    base_query = (
        select(Product)
        .outerjoin(Inventory, Inventory.product_id == Product.id)
        .where(Product.is_active.is_(True))
    )

    # Category filter
    if category and category.strip():
        cat_clean = category.strip().lower()
        base_query = base_query.join(Product.categories).where(
            or_(
                func.lower(ProductCategory.slug) == cat_clean,
                func.lower(ProductCategory.name) == cat_clean,
                func.lower(ProductCategory.slug).contains(cat_clean),
                func.lower(ProductCategory.name).contains(cat_clean),
            )
        )

    # Price / budget filters
    if min_price is not None:
        base_query = base_query.where(Product.price >= min_price)
    if max_price is not None:
        base_query = base_query.where(Product.price <= max_price)

    # Inventory / stock filter
    if in_stock_only or min_quantity > 1:
        base_query = base_query.where(
            func.coalesce(Inventory.available_quantity, 0) >= max(1, min_quantity)
        )

    # Keywords, brand, and feature terms
    terms: list[str] = []
    if brand and brand.strip():
        terms.append(brand.strip())
    if keywords:
        for kw in keywords:
            cleaned = kw.strip()
            if cleaned and cleaned not in terms:
                terms.append(cleaned)
    if required_features:
        for feat in required_features:
            cleaned = feat.strip()
            if cleaned and cleaned not in terms:
                terms.append(cleaned)

    if terms:
        term_conditions = []
        for term in terms:
            variants = get_term_search_variants(term)
            variant_matchers = []
            for v in variants:
                variant_matchers.append(func.lower(Product.name).contains(v))
                variant_matchers.append(func.lower(Product.description).contains(v))
            term_conditions.append(or_(*variant_matchers))
        base_query = base_query.where(and_(*term_conditions))

    # Total count query
    count_query = select(func.count(distinct(Product.id)))
    # Copy from base_query's froms and whereclause
    if base_query.whereclause is not None:
        count_query = (
            select(func.count(distinct(Product.id)))
            .select_from(Product)
            .outerjoin(Inventory, Inventory.product_id == Product.id)
        )
        if category and category.strip():
            count_query = count_query.join(Product.categories)
        count_query = count_query.where(base_query.whereclause)
    else:
        count_query = select(func.count(distinct(Product.id))).select_from(Product)

    total_count = session.scalar(count_query) or 0

    # Sorting
    sort_enum = SortOrder(sort_by) if isinstance(sort_by, str) else sort_by
    if sort_enum == SortOrder.PRICE_ASC:
        base_query = base_query.order_by(Product.price.asc(), Product.name.asc(), Product.id.asc())
    elif sort_enum == SortOrder.PRICE_DESC:
        base_query = base_query.order_by(Product.price.desc(), Product.name.asc(), Product.id.asc())
    elif sort_enum == SortOrder.NAME_ASC:
        base_query = base_query.order_by(Product.name.asc(), Product.id.asc())
    elif sort_enum == SortOrder.NAME_DESC:
        base_query = base_query.order_by(Product.name.desc(), Product.id.asc())
    elif sort_enum == SortOrder.NEWEST:
        base_query = base_query.order_by(Product.created_at.desc(), Product.id.asc())
    else:
        base_query = base_query.order_by(Product.price.asc(), Product.name.asc(), Product.id.asc())

    # Preload relationships and paginate
    paginated_query = (
        base_query.options(
            selectinload(Product.categories),
            selectinload(Product.inventory),
        )
        .distinct()
        .limit(limit)
        .offset(offset)
    )

    products = list(session.scalars(paginated_query).unique())

    logger.info(
        "Catalogue search executed",
        extra={
            "event": "catalogue_search",
            "terms_count": len(terms),
            "has_category": bool(category and category.strip()),
            "has_min_price": min_price is not None,
            "has_max_price": max_price is not None,
            "in_stock_only": in_stock_only,
            "total_count": total_count,
            "returned_count": len(products),
        },
    )

    return products, total_count
