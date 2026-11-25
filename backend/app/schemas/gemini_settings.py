from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class SavePersonalGeminiKeyRequest(BaseModel):
    """Request payload to validate and save an authenticated user's personal Gemini API key."""

    model_config = ConfigDict(extra="forbid")

    api_key: SecretStr = Field(
        ...,
        description="Personal Google Gemini API key to validate and store encrypted.",
    )

    @field_validator("api_key")
    @classmethod
    def validate_api_key_format(cls, value: SecretStr) -> SecretStr:
        raw_key = value.get_secret_value().strip()
        if not raw_key:
            raise ValueError("API key must not be blank.")
        if len(raw_key) < 8 or len(raw_key) > 256:
            raise ValueError("API key length is invalid (must be between 8 and 256 characters).")
        return SecretStr(raw_key)


class ValidatePersonalGeminiKeyRequest(BaseModel):
    """Request payload to test/validate an API key without persisting it."""

    model_config = ConfigDict(extra="forbid")

    api_key: SecretStr = Field(
        ...,
        description="Personal Google Gemini API key to validate without persisting.",
    )

    @field_validator("api_key")
    @classmethod
    def validate_api_key_format(cls, value: SecretStr) -> SecretStr:
        raw_key = value.get_secret_value().strip()
        if not raw_key:
            raise ValueError("API key must not be blank.")
        if len(raw_key) < 8 or len(raw_key) > 256:
            raise ValueError("API key length is invalid (must be between 8 and 256 characters).")
        return SecretStr(raw_key)


class TogglePersonalGeminiKeyRequest(BaseModel):
    """Request payload to enable or disable an existing stored Gemini API key."""

    model_config = ConfigDict(extra="forbid")

    is_enabled: bool = Field(
        ...,
        description="Whether the stored personal key should be enabled for agent requests.",
    )


class ValidatePersonalGeminiKeyResponse(BaseModel):
    """Response payload for key validation attempts."""

    model_config = ConfigDict(from_attributes=True)

    is_valid: bool = Field(
        ...,
        description="Whether the key was successfully verified by Google Gemini.",
    )
    status: str = Field(
        ...,
        description="Status code: VALID, INVALID_KEY, QUOTA_EXHAUSTED, MODEL_UNAVAILABLE, or TEMPORARY_FAILURE.",
    )
    message: str = Field(
        ...,
        description="Sanitized human-readable explanation of validation outcome.",
    )
    masked_key: str | None = Field(
        default=None,
        description="Safely masked key hint showing only the last four characters.",
    )


class PersonalGeminiKeyResponse(BaseModel):
    """Response payload exposing safe metadata of the user's stored Gemini key.
    
    Never exposes plaintext keys or encrypted ciphertext tokens.
    """

    model_config = ConfigDict(from_attributes=True)

    has_key: bool = Field(
        ...,
        description="Whether the authenticated user currently has a stored Gemini key.",
    )
    is_enabled: bool = Field(
        default=False,
        description="Whether the user's key is enabled.",
    )
    key_hint: str | None = Field(
        default=None,
        description="Masked hint of the API key showing only the final four characters.",
    )
    status: str | None = Field(
        default=None,
        description="Operational status of the stored key (e.g. ACTIVE, RATE_LIMITED).",
    )
    last_validated_at: datetime | None = Field(
        default=None,
        description="UTC timestamp of the most recent successful validation.",
    )
    last_used_at: datetime | None = Field(
        default=None,
        description="UTC timestamp when this key was last used for an agent request.",
    )
    cooldown_until: datetime | None = Field(
        default=None,
        description="UTC timestamp until which this key is temporarily cooling down.",
    )
    created_at: datetime | None = Field(
        default=None,
        description="UTC timestamp when the key was initially stored.",
    )
    updated_at: datetime | None = Field(
        default=None,
        description="UTC timestamp of the most recent key update or toggle.",
    )
