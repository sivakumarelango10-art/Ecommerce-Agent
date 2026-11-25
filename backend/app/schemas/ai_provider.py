from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr


# ---------------------------------------------------------------------------
# Provider Schemas
# ---------------------------------------------------------------------------

class AIProviderCreate(BaseModel):
    code: str = Field(min_length=2, max_length=64, pattern=r"^[a-z0-9_-]+$")
    display_name: str = Field(min_length=1, max_length=128)
    adapter_type: str = Field(default="unsupported", max_length=64)
    base_url: str | None = Field(default=None, max_length=512)
    documentation_url: str | None = Field(default=None, max_length=512)
    is_enabled: bool = True
    priority: int = Field(default=100, ge=1, le=1000)
    connection_status: str = Field(default="PLANNED")


class AIProviderUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=128)
    adapter_type: str | None = Field(default=None, max_length=64)
    base_url: str | None = Field(default=None, max_length=512)
    documentation_url: str | None = Field(default=None, max_length=512)
    is_enabled: bool | None = None
    priority: int | None = Field(default=None, ge=1, le=1000)
    connection_status: str | None = None


class AIProviderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    code: str
    display_name: str
    adapter_type: str
    base_url: str | None
    documentation_url: str | None
    is_enabled: bool
    priority: int
    connection_status: str
    created_at: datetime
    updated_at: datetime
    models_count: int | None = None
    keys_count: int | None = None


# ---------------------------------------------------------------------------
# Model Schemas
# ---------------------------------------------------------------------------

class AIModelCreate(BaseModel):
    provider_id: UUID
    model_identifier: str = Field(min_length=1, max_length=128)
    display_name: str = Field(min_length=1, max_length=128)
    description: str | None = None
    supports_text: bool = True
    supports_vision: bool = False
    supports_tools: bool = False
    supports_streaming: bool = True
    supports_json_mode: bool = False
    supports_embeddings: bool = False
    supports_speech_input: bool = False
    supports_speech_output: bool = False
    context_window: int | None = Field(default=None, gt=0)
    max_output_tokens: int | None = Field(default=None, gt=0)
    pricing_input_per_million: Decimal | None = Field(default=None, ge=0)
    pricing_output_per_million: Decimal | None = Field(default=None, ge=0)
    pricing_currency: str = Field(default="USD", min_length=3, max_length=3)
    access_class: str = Field(default="UNKNOWN")
    free_tier_limit_metadata: dict[str, Any] | None = None
    terms_url: str | None = Field(default=None, max_length=512)
    pricing_url: str | None = Field(default=None, max_length=512)
    is_enabled: bool = True
    is_default: bool = False


class AIModelUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = None
    supports_text: bool | None = None
    supports_vision: bool | None = None
    supports_tools: bool | None = None
    supports_streaming: bool | None = None
    supports_json_mode: bool | None = None
    supports_embeddings: bool | None = None
    supports_speech_input: bool | None = None
    supports_speech_output: bool | None = None
    context_window: int | None = Field(default=None, gt=0)
    max_output_tokens: int | None = Field(default=None, gt=0)
    pricing_input_per_million: Decimal | None = Field(default=None, ge=0)
    pricing_output_per_million: Decimal | None = Field(default=None, ge=0)
    pricing_currency: str | None = Field(default=None, min_length=3, max_length=3)
    access_class: str | None = None
    free_tier_limit_metadata: dict[str, Any] | None = None
    terms_url: str | None = Field(default=None, max_length=512)
    pricing_url: str | None = Field(default=None, max_length=512)
    last_pricing_verified_at: datetime | None = None
    is_enabled: bool | None = None
    is_default: bool | None = None


class AIModelResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    provider_id: UUID
    model_identifier: str
    display_name: str
    description: str | None
    supports_text: bool
    supports_vision: bool
    supports_tools: bool
    supports_streaming: bool
    supports_json_mode: bool
    supports_embeddings: bool
    supports_speech_input: bool
    supports_speech_output: bool
    context_window: int | None
    max_output_tokens: int | None
    pricing_input_per_million: Decimal | None
    pricing_output_per_million: Decimal | None
    pricing_currency: str
    access_class: str
    free_tier_limit_metadata: dict[str, Any] | None
    terms_url: str | None
    pricing_url: str | None
    last_pricing_verified_at: datetime | None
    is_enabled: bool
    is_default: bool
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Platform AI Key Schemas
# ---------------------------------------------------------------------------

class PlatformAIKeyCreate(BaseModel):
    provider_id: UUID
    api_key: SecretStr = Field(min_length=1, description="Raw plaintext API key, encrypted immediately.")
    quota_group: str = Field(min_length=1, max_length=64, description="Mandatory quota bucket/project grouping.")
    key_label: str = Field(default="Default Key", min_length=1, max_length=100)
    is_enabled: bool = True


class PlatformAIKeyUpdate(BaseModel):
    quota_group: str | None = Field(default=None, min_length=1, max_length=64)
    key_label: str | None = Field(default=None, min_length=1, max_length=100)
    is_enabled: bool | None = None
    status: str | None = None


class PlatformAIKeyResponse(BaseModel):
    """Safe response model. NEVER includes raw plaintext key or encrypted ciphertext."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    provider_id: UUID
    key_hint: str
    quota_group: str
    key_label: str
    is_enabled: bool
    status: str
    cooldown_until: datetime | None = None
    last_validated_at: datetime | None = None
    last_used_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class CatalogueSeedResponse(BaseModel):
    providers_created: int
    providers_updated: int
    models_created: int
