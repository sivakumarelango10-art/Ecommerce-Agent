from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.app.db.session import get_db_session
from backend.app.schemas.catalog import (
    InventoryAvailabilityResponse,
    ProductCategoryResponse,
    ProductResponse,
)
from backend.app.services.catalog_service import (
    get_inventory_availability,
    get_product,
    list_categories,
    list_products,
)

router = APIRouter(tags=["catalog"])


@router.get("/products", response_model=list[ProductResponse])
def get_products(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    category: str | None = Query(default=None, min_length=1, max_length=120),
    session: Session = Depends(get_db_session),
) -> list[ProductResponse]:
    return [
        ProductResponse.model_validate(product)
        for product in list_products(
            session,
            limit=limit,
            offset=offset,
            category_slug=category,
        )
    ]


@router.get("/products/{product_id}", response_model=ProductResponse)
def get_product_details(
    product_id: UUID,
    session: Session = Depends(get_db_session),
) -> ProductResponse:
    product = get_product(session, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found.")
    return ProductResponse.model_validate(product)


@router.get(
    "/products/{product_id}/availability",
    response_model=InventoryAvailabilityResponse,
)
def get_product_availability(
    product_id: UUID,
    session: Session = Depends(get_db_session),
) -> InventoryAvailabilityResponse:
    availability = get_inventory_availability(session, product_id)
    if availability is None:
        raise HTTPException(status_code=404, detail="Product not found.")
    available, quantity = availability
    return InventoryAvailabilityResponse(
        product_id=product_id,
        available=available,
        available_quantity=quantity,
    )


@router.get("/categories", response_model=list[ProductCategoryResponse])
def get_categories(
    session: Session = Depends(get_db_session),
) -> list[ProductCategoryResponse]:
    return [
        ProductCategoryResponse.model_validate(category)
        for category in list_categories(session)
    ]
