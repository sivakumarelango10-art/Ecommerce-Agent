from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from backend.app.schemas.order import ShippingAddressSnapshotResponse


class GuestVerificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order_number: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="Public order identifier (e.g. ORD-20261121-1A2B3C4D)",
    )
    email: EmailStr = Field(
        ..., description="Email address associated with the order at checkout"
    )


class GenericVerificationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = "verification_initiated"
    message: str = (
        "If the order and email match our records, a verification code has been sent."
    )


class GuestVerifyOTPRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order_number: str = Field(
        ..., min_length=1, max_length=64, description="Public order identifier"
    )
    email: EmailStr = Field(
        ..., description="Email address associated with the order"
    )
    otp: str = Field(
        ...,
        min_length=6,
        max_length=6,
        pattern=r"^\d{6}$",
        description="6-digit numeric verification code",
    )


class GuestTokenResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    access_token: str
    token_type: str = "Bearer"
    expires_in: int
    order_number: str


class GuestOrderItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    product_sku: str
    product_name: str
    quantity: int = Field(..., ge=1)
    unit_price: Decimal = Field(..., max_digits=12, decimal_places=2)
    line_total: Decimal = Field(..., max_digits=12, decimal_places=2)


class GuestOrderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    order_number: str
    created_at: datetime
    status: str
    currency: str
    subtotal: Decimal = Field(..., max_digits=12, decimal_places=2)
    shipping_fee: Decimal = Field(..., max_digits=12, decimal_places=2)
    tax: Decimal = Field(..., max_digits=12, decimal_places=2)
    total_amount: Decimal = Field(..., max_digits=12, decimal_places=2)
    shipping_snapshot: ShippingAddressSnapshotResponse
    items: list[GuestOrderItemResponse] = Field(default_factory=list)
