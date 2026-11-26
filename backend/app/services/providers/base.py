from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field
import json
import re
from typing import Any, TypeVar, get_args, get_origin

from pydantic import BaseModel, ValidationError


@dataclass(frozen=True)
class ProviderCapabilities:
    """Capabilities and limits supported by a provider adapter."""

    supports_text: bool = True
    supports_structured_output: bool = False
    supports_function_calling: bool = False
    supports_streaming: bool = False
    supports_vision: bool = False
    max_context_window: int | None = None
    max_output_tokens: int | None = None


@dataclass(frozen=True)
class TokenUsage:
    """Token usage counts reported by a provider without fabrication."""

    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None

    def to_dict(self) -> dict[str, int | None]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
        }


@dataclass
class GenerationRequest:
    """Provider-independent generation request specification.

    Provider-specific formats and payloads are constructed exclusively inside adapters.
    """

    prompt: str | None = None
    contents: Any = None
    model: str | None = None
    system_instruction: str | None = None
    tools: Sequence[Any] | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    timeout_seconds: float = 30.0
    correlation_id: str | None = None
    user_key: str | None = None
    response_schema: type[BaseModel] | None = None
    strict_schema: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class GenerationResponse:
    """Provider-independent generation response structure."""

    text: str | None = None
    parsed: Any = None
    function_calls: tuple[Any, ...] = ()
    raw_content: Any = None
    usage: TokenUsage | None = None
    model: str = ""
    provider: str = ""
    latency_ms: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


class AIProviderError(Exception):
    """Base exception for all normalized provider errors."""

    def __init__(
        self,
        message: str,
        *,
        provider_code: str,
        model: str | None = None,
        status_code: int | None = None,
        is_retryable: bool = False,
        retry_after_seconds: float | None = None,
        error_category: str = "PROVIDER_ERROR",
    ) -> None:
        super().__init__(message)
        self.message = message
        self.provider_code = provider_code
        self.model = model
        self.status_code = status_code
        self.is_retryable = is_retryable
        self.retry_after_seconds = retry_after_seconds
        self.error_category = error_category

    def __str__(self) -> str:
        parts = [f"[{self.provider_code}]", self.message]
        if self.status_code is not None:
            parts.append(f"(status={self.status_code})")
        if self.retry_after_seconds is not None:
            parts.append(f"(retry_after={self.retry_after_seconds}s)")
        return " ".join(parts)


class AIProviderTimeoutError(AIProviderError):
    """Raised when an AI provider request exceeds its configured timeout."""

    def __init__(
        self,
        message: str,
        *,
        provider_code: str,
        model: str | None = None,
        status_code: int | None = 408,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(
            message,
            provider_code=provider_code,
            model=model,
            status_code=status_code,
            is_retryable=True,
            retry_after_seconds=retry_after_seconds,
            error_category="TIMEOUT",
        )


class AIProviderRateLimitError(AIProviderError):
    """Raised when an AI provider enforces rate limits (e.g. 429 RPM/TPM) and request can be retried."""

    def __init__(
        self,
        message: str,
        *,
        provider_code: str,
        model: str | None = None,
        status_code: int | None = 429,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(
            message,
            provider_code=provider_code,
            model=model,
            status_code=status_code,
            is_retryable=True,
            retry_after_seconds=retry_after_seconds,
            error_category="RATE_LIMIT",
        )


class AIProviderQuotaExhaustedError(AIProviderError):
    """Raised when an AI provider quota is exhausted (e.g. daily limit RPD, balance exhausted)."""

    def __init__(
        self,
        message: str,
        *,
        provider_code: str,
        model: str | None = None,
        status_code: int | None = 429,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(
            message,
            provider_code=provider_code,
            model=model,
            status_code=status_code,
            is_retryable=False,
            retry_after_seconds=retry_after_seconds,
            error_category="QUOTA_EXHAUSTED",
        )


class AIProviderAuthenticationError(AIProviderError):
    """Raised when provider authentication fails (e.g. 401, 403, invalid or revoked API key)."""

    def __init__(
        self,
        message: str,
        *,
        provider_code: str,
        model: str | None = None,
        status_code: int | None = 401,
    ) -> None:
        super().__init__(
            message,
            provider_code=provider_code,
            model=model,
            status_code=status_code,
            is_retryable=False,
            retry_after_seconds=None,
            error_category="AUTHENTICATION_FAILED",
        )


class AIProviderTransientError(AIProviderError):
    """Raised on transient provider server errors (e.g. 500, 502, 503, 504) that can be retried."""

    def __init__(
        self,
        message: str,
        *,
        provider_code: str,
        model: str | None = None,
        status_code: int | None = 503,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(
            message,
            provider_code=provider_code,
            model=model,
            status_code=status_code,
            is_retryable=True,
            retry_after_seconds=retry_after_seconds,
            error_category="TRANSIENT_ERROR",
        )


class AIProviderBadRequestError(AIProviderError):
    """Raised on invalid request format or invalid parameters (e.g. 400)."""

    def __init__(
        self,
        message: str,
        *,
        provider_code: str,
        model: str | None = None,
        status_code: int | None = 400,
    ) -> None:
        super().__init__(
            message,
            provider_code=provider_code,
            model=model,
            status_code=status_code,
            is_retryable=False,
            retry_after_seconds=None,
            error_category="BAD_REQUEST",
        )


class AIProviderResponseError(AIProviderError):
    """Raised when a provider returns an empty, unparseable, or invalid response."""

    def __init__(
        self,
        message: str,
        *,
        provider_code: str,
        model: str | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(
            message,
            provider_code=provider_code,
            model=model,
            status_code=status_code,
            is_retryable=False,
            retry_after_seconds=None,
            error_category="EMPTY_RESPONSE",
        )


class AIProviderValidationError(AIProviderResponseError):
    """Raised when model output fails validation against the requested schema."""

    def __init__(
        self,
        message: str,
        *,
        provider_code: str,
        model: str | None = None,
        raw_text: str | None = None,
    ) -> None:
        super().__init__(
            message,
            provider_code=provider_code,
            model=model,
        )
        self.error_category = "VALIDATION_ERROR"
        self.raw_text = raw_text


class AIProviderSchemaError(AIProviderBadRequestError):
    """Raised when a requested response schema is invalid, too large, or deeply nested."""

    def __init__(
        self,
        message: str,
        *,
        provider_code: str = "orchestrator",
        model: str | None = None,
    ) -> None:
        super().__init__(
            message,
            provider_code=provider_code,
            model=model,
            status_code=400,
        )
        self.error_category = "INVALID_SCHEMA"


T = TypeVar("T", bound=BaseModel)

MAX_SCHEMA_FIELDS = 50
MAX_SCHEMA_DEPTH = 5


def _extract_nested_models(annotation: Any) -> list[type[BaseModel]]:
    """Extract nested BaseModel classes from typing annotations."""
    models: list[type[BaseModel]] = []
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        models.append(annotation)
        return models

    origin = get_origin(annotation)
    if origin is not None:
        for arg in get_args(annotation):
            models.extend(_extract_nested_models(arg))
    return models


def validate_structured_schema(
    schema: Any,
    *,
    max_fields: int = MAX_SCHEMA_FIELDS,
    max_depth: int = MAX_SCHEMA_DEPTH,
) -> type[BaseModel]:
    """Validate that the provided schema is a trusted, bound Pydantic model.

    Enforces:
    1. Schema must be a subclass of pydantic.BaseModel.
    2. Prevents excessively large schemas (> max_fields total fields).
    3. Prevents deeply nested schemas (> max_depth levels).
    """
    if not (isinstance(schema, type) and issubclass(schema, BaseModel)):
        raise AIProviderSchemaError(
            f"Invalid schema: expected a subclass of pydantic.BaseModel, got {type(schema).__name__}"
        )

    def _inspect_depth_and_fields(model_cls: type[BaseModel], current_depth: int) -> int:
        if current_depth > max_depth:
            raise AIProviderSchemaError(
                f"Schema '{model_cls.__name__}' exceeds maximum allowed nesting depth of {max_depth}."
            )
        field_count = len(model_cls.model_fields)
        for field_name, field_info in model_cls.model_fields.items():
            nested_classes = _extract_nested_models(field_info.annotation)
            for nested in nested_classes:
                field_count += _inspect_depth_and_fields(nested, current_depth + 1)
        return field_count

    total_fields = _inspect_depth_and_fields(schema, 1)
    if total_fields > max_fields:
        raise AIProviderSchemaError(
            f"Schema '{schema.__name__}' exceeds maximum allowed field count of {max_fields} (found {total_fields})."
        )

    return schema


def parse_and_validate_structured_output(
    raw_text: str | None,
    schema: type[T],
    *,
    strict: bool = True,
    provider_code: str = "ai_provider",
    model: str | None = None,
) -> T:
    """Parse and validate JSON response text against a trusted Pydantic schema."""
    if not raw_text or not raw_text.strip():
        raise AIProviderResponseError(
            f"Provider '{provider_code}' returned an empty response for structured generation.",
            provider_code=provider_code,
            model=model,
        )

    clean_text = raw_text.strip()
    if clean_text.startswith("```"):
        clean_text = re.sub(r"^```(?:json)?\s*", "", clean_text)
        clean_text = re.sub(r"\s*```$", "", clean_text)
        clean_text = clean_text.strip()

    try:
        data = json.loads(clean_text)
    except json.JSONDecodeError as exc:
        raise AIProviderValidationError(
            f"Provider '{provider_code}' returned malformed JSON: {str(exc)}",
            provider_code=provider_code,
            model=model,
            raw_text=raw_text,
        ) from exc

    if not isinstance(data, dict):
        raise AIProviderValidationError(
            f"Expected JSON object (dict), but got {type(data).__name__}.",
            provider_code=provider_code,
            model=model,
            raw_text=raw_text,
        )

    if strict:
        extra_keys = set(data.keys()) - set(schema.model_fields.keys())
        if extra_keys:
            raise AIProviderValidationError(
                f"Unexpected fields in structured output under strict validation: {sorted(extra_keys)}",
                provider_code=provider_code,
                model=model,
                raw_text=raw_text,
            )

    try:
        return schema.model_validate(data)
    except ValidationError as exc:
        raise AIProviderValidationError(
            f"Structured output schema validation failed: {str(exc)}",
            provider_code=provider_code,
            model=model,
            raw_text=raw_text,
        ) from exc


class AIProviderAdapter(ABC):
    """Abstract base class contract for all provider-specific runtime adapters."""

    @property
    @abstractmethod
    def provider_code(self) -> str:
        """Unique provider code identifier (e.g. 'google_gemini')."""

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Human-readable provider display name."""

    @abstractmethod
    def get_capabilities(self) -> ProviderCapabilities:
        """Return the capabilities and limitations supported by this provider."""

    @abstractmethod
    def supports_model(self, model: str) -> bool:
        """Check whether the given model identifier is supported by this adapter."""

    @abstractmethod
    def generate(self, request: GenerationRequest) -> GenerationResponse:
        """Execute a generation request synchronously."""

    @abstractmethod
    def generate_text(self, prompt: str, model: str | None = None, **kwargs: Any) -> str:
        """Execute text generation synchronously, returning plain response text."""

    @abstractmethod
    def health_check(self) -> dict[str, Any]:
        """Return provider runtime health status snapshot."""

    async def generate_async(self, request: GenerationRequest) -> GenerationResponse:
        """Execute a generation request asynchronously via threadpool worker."""
        import asyncio

        return await asyncio.to_thread(self.generate, request)

    async def generate_text_async(
        self, prompt: str, model: str | None = None, **kwargs: Any
    ) -> str:
        """Execute text generation asynchronously, returning plain response text."""
        import asyncio

        return await asyncio.to_thread(self.generate_text, prompt, model=model, **kwargs)

    async def health_check_async(self) -> dict[str, Any]:
        """Return provider runtime health status asynchronously."""
        import asyncio

        return await asyncio.to_thread(self.health_check)
