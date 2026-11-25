from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any


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
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class GenerationResponse:
    """Provider-independent generation response structure."""

    text: str | None = None
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
