from pydantic import Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError


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


def load_settings(*, env_file: str | None = ".env") -> Settings:
    try:
        return Settings(_env_file=env_file)
    except ValidationError as exc:
        raise ConfigurationError(
            "Application configuration is invalid. Check the required Gemini, database, and authentication settings."
        ) from exc
