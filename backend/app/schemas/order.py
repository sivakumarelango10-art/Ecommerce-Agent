from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CheckoutItemAcknowledgement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: UUID
    quantity: int = Field(..., ge=1, description="Quantity of product in cart to checkout, must be >= 1")
    acknowledged_unit_price: Decimal = Field(
        ...,
        max_digits=12,
        decimal_places=2,
        ge=0,
        description="Unit price customer viewed and accepted",
    )


class CheckoutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shipping_address_id: UUID
    items: list[CheckoutItemAcknowledgement] = Field(
        ...,
        min_length=1,
        description="Snapshot of cart items customer has acknowledged and requested to purchase",
    )

    @field_validator("items")
    @classmethod
    def reject_duplicate_product_ids(
        cls, items: list[CheckoutItemAcknowledgement]
    ) -> list[CheckoutItemAcknowledgement]:
        seen: set[UUID] = set()
        for item in items:
            if item.product_id in seen:
                raise ValueError(f"Duplicate product_id in items acknowledgement: {item.product_id}")
            seen.add(item.product_id)
        return items


class PriceChangeDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    product_id: UUID
    product_sku: str
    product_name: str
    acknowledged_price: Decimal = Field(..., max_digits=12, decimal_places=2)
    current_price: Decimal = Field(..., max_digits=12, decimal_places=2)
    currency: str


class PriceChangeErrorResponse(BaseModel):
    code: str = "PRICE_CHANGED"
    detail: str
    price_changes: list[PriceChangeDetail]


class OrderItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    product_id: UUID | None
    product_sku: str
    product_name: str
    unit_price: Decimal = Field(..., max_digits=12, decimal_places=2)
    quantity: int = Field(..., ge=1)
    line_total: Decimal = Field(..., max_digits=12, decimal_places=2)
    created_at: datetime


class ShippingAddressSnapshotResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    recipient_name: str
    line1: str
    line2: str | None = None
    city: str
    region: str
    postal_code: str
    country_code: str


class OrderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    order_number: str
    user_id: UUID
    status: str
    currency: str
    subtotal: Decimal = Field(..., max_digits=12, decimal_places=2)
    shipping_fee: Decimal = Field(..., max_digits=12, decimal_places=2)
    tax: Decimal = Field(..., max_digits=12, decimal_places=2)
    total_amount: Decimal = Field(..., max_digits=12, decimal_places=2)
    shipping_address_id: UUID | None
    shipping_snapshot: ShippingAddressSnapshotResponse
    items: list[OrderItemResponse] = Field(default_factory=list)
    idempotency_key: str | None = None
    request_fingerprint: str | None = None
    created_at: datetime
    updated_at: datetime


class OrderSummaryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    order_number: str
    user_id: UUID
    status: str
    currency: str
    total_amount: Decimal = Field(..., max_digits=12, decimal_places=2)
    item_count: int
    created_at: datetime


class CustomerOrderItemSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    product_sku: str
    product_name: str
    quantity: int = Field(..., ge=1)
    unit_price: Decimal = Field(..., max_digits=12, decimal_places=2)
    line_total: Decimal = Field(..., max_digits=12, decimal_places=2)


class CustomerOrderStatusSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    order_number: str
    status: str
    currency: str
    total_amount: Decimal = Field(..., max_digits=12, decimal_places=2)
    item_count: int
    created_at: datetime
    updated_at: datetime


class CustomerOrderDetailSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    order_number: str
    status: str
    currency: str
    subtotal: Decimal = Field(..., max_digits=12, decimal_places=2)
    shipping_fee: Decimal = Field(..., max_digits=12, decimal_places=2)
    tax: Decimal = Field(..., max_digits=12, decimal_places=2)
    total_amount: Decimal = Field(..., max_digits=12, decimal_places=2)
    created_at: datetime
    shipping_snapshot: ShippingAddressSnapshotResponse
    items: list[CustomerOrderItemSummary] = Field(default_factory=list)
