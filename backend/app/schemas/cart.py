from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CartItemAddRequest(BaseModel):
    product_id: UUID
    quantity: int = Field(default=1, ge=1, description="Quantity to add, must be >= 1")


class CartItemUpdateRequest(BaseModel):
    quantity: int = Field(..., ge=1, description="New quantity, must be >= 1")


class CartItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    cart_id: UUID
    product_id: UUID
    product_sku: str
    product_name: str
    unit_price: Decimal = Field(..., max_digits=12, decimal_places=2)
    quantity: int = Field(..., ge=1)
    subtotal: Decimal = Field(..., max_digits=12, decimal_places=2)
    line_total: Decimal = Field(..., max_digits=12, decimal_places=2)
    created_at: datetime
    updated_at: datetime


class CartResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    items: list[CartItemResponse] = Field(default_factory=list)
    subtotal: Decimal = Field(..., max_digits=12, decimal_places=2)
    currency: str
    item_count: int = Field(default=0, description="Total distinct items in cart")
    total_quantity: int = Field(default=0, description="Total product units in cart")
    created_at: datetime
    updated_at: datetime
