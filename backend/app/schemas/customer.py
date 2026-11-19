from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CustomerProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    display_name: str = Field(min_length=1, max_length=120)
    phone: str | None = Field(default=None, max_length=32)


class CustomerProfileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: UUID
    display_name: str
    phone: str | None
    created_at: datetime
    updated_at: datetime


class AddressCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    label: str = Field(default="home", min_length=1, max_length=40)
    recipient_name: str = Field(min_length=1, max_length=120)
    line1: str = Field(min_length=1, max_length=200)
    line2: str | None = Field(default=None, max_length=200)
    city: str = Field(min_length=1, max_length=100)
    region: str = Field(min_length=1, max_length=100)
    postal_code: str = Field(min_length=1, max_length=24)
    country_code: str = Field(default="US", min_length=2, max_length=2)
    is_default: bool = False


class AddressUpdate(AddressCreate):
    pass


class AddressResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    label: str
    recipient_name: str
    line1: str
    line2: str | None
    city: str
    region: str
    postal_code: str
    country_code: str
    is_default: bool
    created_at: datetime
    updated_at: datetime
