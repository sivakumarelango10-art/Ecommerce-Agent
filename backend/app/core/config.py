from dataclasses import dataclass
import json
import logging
from typing import Annotated, Any
from pydantic import Field, PrivateAttr, SecretStr, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict, SettingsError
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from backend.app.core.constants import (
    DEFAULT_CATALOGUE_CURRENCY,
    ISO_4217_CURRENCY_CODES,
)

logger = logging.getLogger(__name__)


class ConfigurationError(RuntimeError):
    """Raised when required application configuration is missing or invalid."""


def _blank_secret_to_none(value: SecretStr | None) -> SecretStr | None:
    """Normalize empty or whitespace-only optional secrets from dashboard/env forms to None."""
    if value is None or not value.get_secret_value().strip():
        return None
    return value


def _validate_min_secret_bytes(
    value: SecretStr | None,
    env_name: str,
    min_bytes: int,
) -> SecretStr | None:
    """Normalize blank optional secrets to None and enforce minimum UTF-8 byte length when set."""
    cleaned = _blank_secret_to_none(value)
    if cleaned is None:
        return None
    if len(cleaned.get_secret_value().strip().encode("utf-8")) < min_bytes:
        raise ValueError(f"{env_name} must be at least {min_bytes} bytes")
    return cleaned


def _check_secret_distinct(
    secret_value: SecretStr | None,
    secret_env_name: str,
    disallowed_sources: list[tuple[SecretStr | None, str]],
) -> None:
    """Ensure a configured encryption secret does not reuse another platform secret."""
    if secret_value is None:
        return
    candidate = secret_value.get_secret_value().strip()
    if not candidate:
        return
    for other_value, other_env_name in disallowed_sources:
        if other_value is not None and candidate == other_value.get_secret_value().strip():
            raise ValueError(f"{secret_env_name} must not reuse {other_env_name}")


def _format_safe_validation_errors(exc: ValidationError) -> str:
    """Summarize Pydantic validation errors using field names and reasons without exposing inputs."""
    parts: list[str] = []
    for err in exc.errors(include_url=False, include_context=False, include_input=False):
        loc = ".".join(str(item) for item in err.get("loc", ()))
        msg = str(err.get("msg", "Invalid value"))
        if msg.startswith("Value error, "):
            msg = msg[len("Value error, "):]
        if loc and not msg.upper().startswith(loc.upper()):
            parts.append(f"{loc}: {msg}")
        else:
            parts.append(msg)
    return "; ".join(parts) if parts else "Invalid settings configuration"


@dataclass(frozen=True)
class GeminiKeyConfig:
    key: SecretStr
    quota_group: str


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    gemini_api_key: SecretStr | None = Field(
        default=None,
        validation_alias="GEMINI_API_KEY",
    )
    gemini_api_keys: SecretStr | str | list[dict[str, Any]] | None = Field(
        default=None,
        validation_alias="GEMINI_API_KEYS",
    )
    _platform_gemini_keys: list[GeminiKeyConfig] = PrivateAttr(default_factory=list)

    @property
    def platform_gemini_keys(self) -> list[GeminiKeyConfig]:
        return self._platform_gemini_keys
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
    database_url_unpooled: SecretStr | None = Field(
        default=None,
        validation_alias="DATABASE_URL_UNPOOLED",
    )
    neon_branch: str | None = Field(
        default=None,
        validation_alias="NEON_BRANCH",
    )
    neon_branch_id: str | None = Field(
        default=None,
        validation_alias="NEON_BRANCH_ID",
    )
    neon_function_host_suffix: str | None = Field(
        default=None,
        validation_alias="NEON_FUNCTION_HOST_SUFFIX",
    )
    neon_auth_base_url: str | None = Field(
        default=None,
        validation_alias="NEON_AUTH_BASE_URL",
    )
    neon_auth_jwks_url: str | None = Field(
        default=None,
        validation_alias="NEON_AUTH_JWKS_URL",
    )
    neon_ai_gateway_token: SecretStr | None = Field(
        default=None,
        validation_alias="NEON_AI_GATEWAY_TOKEN",
    )
    neon_ai_gateway_base_url: str | None = Field(
        default=None,
        validation_alias="NEON_AI_GATEWAY_BASE_URL",
    )
    neon_realtime_url: str | None = Field(
        default=None,
        validation_alias="NEON_REALTIME_URL",
    )
    neon_realtime_secret: SecretStr | None = Field(
        default=None,
        validation_alias="NEON_REALTIME_SECRET",
    )
    aws_access_key_id: SecretStr | None = Field(
        default=None,
        validation_alias="AWS_ACCESS_KEY_ID",
    )
    aws_secret_access_key: SecretStr | None = Field(
        default=None,
        validation_alias="AWS_SECRET_ACCESS_KEY",
    )
    aws_endpoint_url_s3: str | None = Field(
        default=None,
        validation_alias="AWS_ENDPOINT_URL_S3",
    )
    aws_region: str | None = Field(
        default=None,
        validation_alias="AWS_REGION",
    )
    auth_secret_key: SecretStr = Field(validation_alias="AUTH_SECRET_KEY")
    access_token_expire_minutes: int = Field(
        default=15,
        gt=0,
        le=1440,
        validation_alias="ACCESS_TOKEN_EXPIRE_MINUTES",
    )
    admin_emails: Annotated[set[str], NoDecode] = Field(
        default_factory=set,
        validation_alias="ADMIN_EMAILS",
    )
    storage_max_upload_bytes: int = Field(
        default=2 * 1024 * 1024,
        gt=0,
        validation_alias="STORAGE_MAX_UPLOAD_BYTES",
    )
    storage_allowed_content_types: Annotated[set[str], NoDecode] = Field(
        default={
            "text/plain",
            "text/markdown",
            "text/csv",
            "application/json",
            "application/octet-stream",
        },
        validation_alias="STORAGE_ALLOWED_CONTENT_TYPES",
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
    user_key_encryption_secret: SecretStr | None = Field(
        default=None,
        validation_alias="USER_KEY_ENCRYPTION_SECRET",
    )
    platform_key_encryption_secret: SecretStr | None = Field(
        default=None,
        validation_alias="PLATFORM_KEY_ENCRYPTION_SECRET",
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
    outbox_worker_enabled: bool = Field(
        default=True,
        validation_alias="OUTBOX_WORKER_ENABLED",
    )
    outbox_poll_interval_seconds: float = Field(
        default=2.0,
        gt=0,
        validation_alias="OUTBOX_POLL_INTERVAL_SECONDS",
    )
    outbox_lease_seconds: int = Field(
        default=60,
        gt=0,
        validation_alias="OUTBOX_LEASE_SECONDS",
    )
    outbox_batch_size: int = Field(
        default=10,
        gt=0,
        validation_alias="OUTBOX_BATCH_SIZE",
    )
    allowed_origins: list[str] | str = Field(
        default=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:3000",
            "http://127.0.0.1:3000",
        ],
        validation_alias="ALLOWED_ORIGINS",
    )
    auth_rate_limit_login_per_minute: int = Field(
        default=10,
        gt=0,
        validation_alias="AUTH_RATE_LIMIT_LOGIN_PER_MINUTE",
    )
    auth_rate_limit_register_per_minute: int = Field(
        default=5,
        gt=0,
        validation_alias="AUTH_RATE_LIMIT_REGISTER_PER_MINUTE",
    )
    auth_rate_limit_window_seconds: int = Field(
        default=60,
        gt=0,
        validation_alias="AUTH_RATE_LIMIT_WINDOW_SECONDS",
    )
    gemini_key_validation_rate_limit_per_minute: int = Field(
        default=5,
        gt=0,
        validation_alias="GEMINI_KEY_VALIDATION_RATE_LIMIT_PER_MINUTE",
    )

    @field_validator("gemini_api_key")
    @classmethod
    def api_key_must_not_be_blank(cls, value: SecretStr | None) -> SecretStr | None:
        # Treat blank GEMINI_API_KEY="" as unset so GEMINI_API_KEYS can supply the key pool;
        # validate_gemini_keys_and_pool still fails closed if neither source is configured.
        return _blank_secret_to_none(value)

    @field_validator(
        "database_url_unpooled",
        "neon_ai_gateway_token",
        "neon_realtime_secret",
        "aws_access_key_id",
        "aws_secret_access_key",
        "smtp_username",
        "smtp_password",
    )
    @classmethod
    def normalize_optional_blank_secrets(cls, value: SecretStr | None) -> SecretStr | None:
        return _blank_secret_to_none(value)

    @field_validator("smtp_host", "smtp_from_email")
    @classmethod
    def normalize_optional_blank_strings(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

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
        if raw_url.startswith("postgresql://"):
            raw_url = "postgresql+psycopg://" + raw_url[len("postgresql://"):]
        elif raw_url.startswith("postgres://"):
            raw_url = "postgresql+psycopg://" + raw_url[len("postgres://"):]
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
        raw = value.get_secret_value()
        if not raw.strip() or len(raw.encode("utf-8")) < 32:
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
        return _validate_min_secret_bytes(value, "OTP_HASH_SECRET", 16)

    @field_validator("user_key_encryption_secret")
    @classmethod
    def user_key_encryption_secret_must_be_adequate(
        cls, value: SecretStr | None
    ) -> SecretStr | None:
        return _validate_min_secret_bytes(value, "USER_KEY_ENCRYPTION_SECRET", 32)

    @field_validator("platform_key_encryption_secret")
    @classmethod
    def platform_key_encryption_secret_must_be_adequate(
        cls, value: SecretStr | None
    ) -> SecretStr | None:
        return _validate_min_secret_bytes(value, "PLATFORM_KEY_ENCRYPTION_SECRET", 32)

    @model_validator(mode="after")
    def validate_gemini_keys_and_pool(self) -> "Settings":
        raw_keys = self.gemini_api_keys
        entries: list[dict[str, Any]] | None = None

        if raw_keys is not None:
            if isinstance(raw_keys, SecretStr):
                raw_str = raw_keys.get_secret_value().strip()
            elif isinstance(raw_keys, str):
                raw_str = raw_keys.strip()
            else:
                raw_str = None

            if raw_str is not None:
                if not raw_str:
                    entries = None
                else:
                    try:
                        parsed = json.loads(raw_str)
                    except Exception:
                        raise ValueError("GEMINI_API_KEYS must be a valid JSON array") from None
                    if not isinstance(parsed, list):
                        raise ValueError("GEMINI_API_KEYS must be a JSON array")
                    entries = parsed
            elif isinstance(raw_keys, list):
                entries = raw_keys
            else:
                raise ValueError("GEMINI_API_KEYS must be a JSON array")

        key_configs: list[GeminiKeyConfig] = []

        if entries is not None:
            if len(entries) == 0:
                raise ValueError("GEMINI_API_KEYS must not be empty if provided")
            if len(entries) > 6:
                raise ValueError("GEMINI_API_KEYS must contain at most 6 keys")

            seen_keys: set[str] = set()
            for item in entries:
                if not isinstance(item, dict):
                    raise ValueError("Each entry in GEMINI_API_KEYS must be a JSON object with 'key' and 'quota_group'")
                if "key" not in item or "quota_group" not in item:
                    raise ValueError("Each entry in GEMINI_API_KEYS must contain 'key' and 'quota_group'")

                raw_k = item["key"]
                if isinstance(raw_k, SecretStr):
                    k_str = raw_k.get_secret_value().strip()
                elif isinstance(raw_k, str):
                    k_str = raw_k.strip()
                else:
                    raise ValueError("API key in GEMINI_API_KEYS must be a non-blank string")

                if not k_str:
                    raise ValueError("API key in GEMINI_API_KEYS must not be blank")

                raw_group = item["quota_group"]
                if not isinstance(raw_group, str) or not raw_group.strip():
                    raise ValueError("quota_group in GEMINI_API_KEYS must be a non-blank string")
                group_str = raw_group.strip()

                if k_str in seen_keys:
                    raise ValueError("GEMINI_API_KEYS contains duplicate API keys")
                seen_keys.add(k_str)
                key_configs.append(GeminiKeyConfig(key=SecretStr(k_str), quota_group=group_str))

        elif self.gemini_api_key is not None and self.gemini_api_key.get_secret_value().strip():
            # Legacy single key backward compatibility
            key_configs = [
                GeminiKeyConfig(
                    key=self.gemini_api_key,
                    quota_group="default",
                )
            ]
        else:
            raise ValueError("Either GEMINI_API_KEY or GEMINI_API_KEYS must be provided")

        self._platform_gemini_keys = key_configs

        # Ensure self.gemini_api_key is populated with at least the first key for backward compatibility
        if self.gemini_api_key is None or not self.gemini_api_key.get_secret_value().strip():
            self.gemini_api_key = key_configs[0].key

        return self

    @model_validator(mode="after")
    def validate_user_key_encryption_secret_rules(self) -> "Settings":
        env = self.environment.strip().lower()
        if env in {"staging", "production"}:
            if self.user_key_encryption_secret is None or not self.user_key_encryption_secret.get_secret_value().strip():
                raise ValueError("USER_KEY_ENCRYPTION_SECRET is required in staging and production")

        gemini_sources: list[tuple[SecretStr | None, str]] = [
            (self.gemini_api_key, "GEMINI_API_KEY"),
            *[(item.key, "GEMINI_API_KEY") for item in self.platform_gemini_keys],
        ]
        auth_secret = getattr(self, "auth_secret_key", None)

        _check_secret_distinct(
            self.user_key_encryption_secret,
            "USER_KEY_ENCRYPTION_SECRET",
            [(auth_secret, "AUTH_SECRET_KEY"), *gemini_sources],
        )
        _check_secret_distinct(
            self.platform_key_encryption_secret,
            "PLATFORM_KEY_ENCRYPTION_SECRET",
            [
                (auth_secret, "AUTH_SECRET_KEY"),
                (self.user_key_encryption_secret, "USER_KEY_ENCRYPTION_SECRET"),
                *gemini_sources,
            ],
        )
        return self

    @field_validator("allowed_origins")
    @classmethod
    def validate_allowed_origins(cls, value: Any) -> list[str]:
        if isinstance(value, str):
            clean = value.strip()
            if not clean:
                return []
            if clean.startswith("[") and clean.endswith("]"):
                try:
                    parsed = json.loads(clean)
                    if isinstance(parsed, list):
                        value = [str(item).strip() for item in parsed if str(item).strip()]
                except Exception:
                    value = [item.strip() for item in clean.split(",") if item.strip()]
            else:
                value = [item.strip() for item in clean.split(",") if item.strip()]
        elif isinstance(value, (list, tuple)):
            value = [str(item).strip() for item in value if str(item).strip()]
        else:
            value = []

        if "*" in value:
            raise ValueError(
                "Wildcard origin '*' is not allowed when credentials support is enabled. Specify explicit origins."
            )
        return value

    @field_validator("admin_emails", mode="before")
    @classmethod
    def validate_admin_emails(cls, value: Any) -> set[str]:
        if value is None:
            return set()
        if isinstance(value, str):
            clean = value.strip()
            if not clean:
                return set()
            if clean.startswith("[") and clean.endswith("]"):
                try:
                    parsed = json.loads(clean)
                    if isinstance(parsed, list):
                        return {str(item).strip().lower() for item in parsed if str(item).strip()}
                except Exception:
                    pass
            return {item.strip().lower() for item in clean.split(",") if item.strip()}
        elif isinstance(value, (list, tuple, set)):
            return {str(item).strip().lower() for item in value if str(item).strip()}
        return set()

    @field_validator("storage_allowed_content_types", mode="before")
    @classmethod
    def validate_storage_allowed_content_types(cls, value: Any) -> set[str]:
        if value is None:
            return {
                "text/plain",
                "text/markdown",
                "text/csv",
                "application/json",
                "application/octet-stream",
            }
        if isinstance(value, str):
            clean = value.strip()
            if not clean:
                return set()
            if clean.startswith("[") and clean.endswith("]"):
                try:
                    parsed = json.loads(clean)
                    if isinstance(parsed, list):
                        return {str(item).strip().lower() for item in parsed if str(item).strip()}
                except Exception:
                    pass
            return {item.strip().lower() for item in clean.split(",") if item.strip()}
        elif isinstance(value, (list, tuple, set)):
            return {str(item).strip().lower() for item in value if str(item).strip()}
        return set()


def load_settings(*, env_file: str | None = ".env") -> Settings:
    try:
        return Settings(_env_file=env_file)
    except ValidationError as exc:
        details = _format_safe_validation_errors(exc)
        logger.error(
            "Application configuration validation failed: %s",
            details,
            extra={"event": "configuration_error", "error_type": type(exc).__name__},
        )
        raise ConfigurationError(
            f"Application configuration is invalid ({details}). "
            "Check the required Gemini, database, encryption, and authentication settings."
        ) from exc
    except SettingsError as exc:
        logger.error(
            "Application settings parsing failed (%s): %s",
            type(exc).__name__,
            exc,
            extra={"event": "configuration_error", "error_type": type(exc).__name__},
        )
        raise ConfigurationError(
            f"Application configuration is invalid ({exc}). "
            "Check environment variable formatting."
        ) from exc
