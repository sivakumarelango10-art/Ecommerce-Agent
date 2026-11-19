from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ProductCategoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    slug: str


class ProductResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    sku: str
    name: str
    description: str | None
    price: Decimal = Field(max_digits=12, decimal_places=2)
    categories: list[ProductCategoryResponse]
    created_at: datetime
    updated_at: datetime


class InventoryAvailabilityResponse(BaseModel):
    product_id: UUID
    available: bool
    available_quantity: int = Field(ge=0)
