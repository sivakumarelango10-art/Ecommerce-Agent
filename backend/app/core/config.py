from pydantic import Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from backend.app.core.constants import (
    DEFAULT_CATALOGUE_CURRENCY,
    ISO_4217_CURRENCY_CODES,
)


class ConfigurationError(RuntimeError):
    """Raised when required application configuration is missing or invalid."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    gemini_api_key: SecretStr = Field(validation_alias="GEMINI_API_KEY")
    gemini_model: str = Field(
        default="gemini-3.8-flash",
        min_length=1,
        validation_alias="GEMINI_MODEL",
    )
    gemini_timeout_seconds: int = Field(
        default=30,
        gt=0,
        le=120,
        validation_alias="GEMINI_TIMEOUT_SECONDS",
    )
    database_url: SecretStr = Field(validation_alias="DATABASE_URL")
    auth_secret_key: SecretStr = Field(validation_alias="AUTH_SECRET_KEY")
    access_token_expire_minutes: int = Field(
        default=15,
        gt=0,
        le=1440,
        validation_alias="ACCESS_TOKEN_EXPIRE_MINUTES",
    )
    catalogue_currency: str = Field(
        default=DEFAULT_CATALOGUE_CURRENCY,
        validation_alias="CATALOGUE_CURRENCY",
    )
    environment: str = Field(
        default="development",
        validation_alias="ENVIRONMENT",
    )
    otp_hash_secret: SecretStr | None = Field(
        default=None,
        validation_alias="OTP_HASH_SECRET",
    )
    smtp_host: str | None = Field(
        default=None,
        validation_alias="SMTP_HOST",
    )
    smtp_port: int = Field(
        default=587,
        ge=1,
        le=65535,
        validation_alias="SMTP_PORT",
    )
    smtp_username: SecretStr | None = Field(
        default=None,
        validation_alias="SMTP_USERNAME",
    )
    smtp_password: SecretStr | None = Field(
        default=None,
        validation_alias="SMTP_PASSWORD",
    )
    smtp_from_email: str | None = Field(
        default=None,
        validation_alias="SMTP_FROM_EMAIL",
    )
    smtp_use_tls: bool = Field(
        default=True,
        validation_alias="SMTP_USE_TLS",
    )
    smtp_use_ssl: bool = Field(
        default=False,
        validation_alias="SMTP_USE_SSL",
    )
    guest_token_expire_minutes: int = Field(
        default=15,
        gt=0,
        le=1440,
        validation_alias="GUEST_TOKEN_EXPIRE_MINUTES",
    )

    @field_validator("gemini_api_key")
    @classmethod
    def api_key_must_not_be_blank(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("GEMINI_API_KEY must not be blank")
        return value

    @field_validator("gemini_model")
    @classmethod
    def model_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("GEMINI_MODEL must not be blank")
        return value.strip()

    @field_validator("database_url")
    @classmethod
    def database_url_must_use_supported_driver(cls, value: SecretStr) -> SecretStr:
        raw_url = value.get_secret_value().strip()
        if not raw_url:
            raise ValueError("DATABASE_URL must not be blank")
        try:
            url = make_url(raw_url)
        except ArgumentError as exc:
            raise ValueError("DATABASE_URL is invalid") from exc
        if url.drivername not in {"postgresql+psycopg", "sqlite", "sqlite+pysqlite"}:
            raise ValueError("DATABASE_URL must use PostgreSQL with psycopg or SQLite")
        return SecretStr(raw_url)

    @field_validator("auth_secret_key")
    @classmethod
    def auth_secret_must_be_long_enough(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value().encode("utf-8")) < 32:
            raise ValueError("AUTH_SECRET_KEY must be at least 32 bytes")
        return value

    @field_validator("catalogue_currency")
    @classmethod
    def catalogue_currency_must_be_valid_iso_code(cls, value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("CATALOGUE_CURRENCY must be a string")
        clean = value.strip().upper()
        if len(clean) != 3 or not clean.isalpha() or clean not in ISO_4217_CURRENCY_CODES:
            raise ValueError(
                f"CATALOGUE_CURRENCY must be a valid 3-letter ISO 4217 currency code (e.g. USD, INR, EUR), got '{value}'"
            )
        return clean

    @field_validator("otp_hash_secret")
    @classmethod
    def otp_hash_secret_must_be_adequate(
        cls, value: SecretStr | None
    ) -> SecretStr | None:
        if value is not None:
            raw = value.get_secret_value().strip()
            if not raw:
                raise ValueError("OTP_HASH_SECRET must not be blank if provided")
            if len(raw.encode("utf-8")) < 16:
                raise ValueError("OTP_HASH_SECRET must be at least 16 bytes")
        return value


def load_settings(*, env_file: str | None = ".env") -> Settings:
    try:
        return Settings(_env_file=env_file)
    except ValidationError as exc:
        raise ConfigurationError(
            "Application configuration is invalid. Check the required Gemini, database, and authentication settings."
        ) from exc
