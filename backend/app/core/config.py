from pydantic import Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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


def load_settings(*, env_file: str | None = ".env") -> Settings:
    try:
        return Settings(_env_file=env_file)
    except ValidationError as exc:
        raise ConfigurationError(
            "Gemini configuration is invalid. Set GEMINI_API_KEY and check GEMINI_MODEL."
        ) from exc
