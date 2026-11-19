import logging
from collections.abc import Sequence
from dataclasses import dataclass

import httpx
from google import genai
from google.genai import errors, types

from backend.app.core.config import Settings
from backend.app.core.constants import MAX_QUESTION_LENGTH

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GeminiResult:
    text: str | None
    function_calls: tuple[types.FunctionCall, ...]


_SYSTEM_INSTRUCTION = (
    "You are an e-commerce assistant for general product and shopping questions. "
    "You have no access to customer accounts, orders, payments, or inventory. "
    "Do not claim to retrieve or change private customer data or complete transactions."
)


class GeminiServiceError(RuntimeError):
    """Base error for Gemini request and response failures."""


class GeminiTimeoutError(GeminiServiceError):
    """Raised when the Gemini request exceeds its configured timeout."""


class GeminiProviderError(GeminiServiceError):
    """Raised when the Gemini API or transport reports a request failure."""


class GeminiResponseError(GeminiServiceError):
    """Raised when Gemini returns no usable text response."""


class GeminiService:
    def __init__(self, settings: Settings) -> None:
        api_key = settings.gemini_api_key.get_secret_value()
        if not api_key.strip():
            raise ValueError("GEMINI_API_KEY must not be blank")

        self._model = settings.gemini_model
        self._client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(
                timeout=settings.gemini_timeout_seconds * 1000
            ),
        )

    def generate(
        self,
        question: str,
        *,
        tools: Sequence[types.Tool] | None = None,
    ) -> GeminiResult:
        question = question.strip()
        if not question:
            raise ValueError("question must not be blank")
        if len(question) > MAX_QUESTION_LENGTH:
            raise ValueError(
                f"question must not exceed {MAX_QUESTION_LENGTH} characters"
            )

        config = types.GenerateContentConfig(
            system_instruction=_SYSTEM_INSTRUCTION,
            tools=list(tools) if tools else None,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(
                disable=True
            ),
        )

        try:
            response = self._client.models.generate_content(
                model=self._model,
                contents=question,
                config=config,
            )
        except (httpx.TimeoutException, TimeoutError) as exc:
            logger.warning(
                "Gemini request timed out",
                extra={"event": "gemini_timeout", "model": self._model},
            )
            raise GeminiTimeoutError("Gemini request timed out.") from exc
        except httpx.RequestError as exc:
            logger.error(
                "Gemini transport request failed",
                extra={
                    "event": "gemini_transport_error",
                    "model": self._model,
                    "error_type": type(exc).__name__,
                },
            )
            raise GeminiProviderError("Gemini request failed.") from exc
        except errors.APIError as exc:
            logger.error(
                "Gemini API request failed",
                extra={
                    "event": "gemini_api_error",
                    "model": self._model,
                    "status_code": exc.code,
                },
            )
            raise GeminiProviderError("Gemini request failed.") from exc

        response_text = response.text
        text = (
            response_text.strip()
            if isinstance(response_text, str) and response_text.strip()
            else None
        )
        function_calls = tuple(response.function_calls or ())
        if text is None and not function_calls:
            logger.warning(
                "Gemini returned no usable content",
                extra={"event": "gemini_empty_response", "model": self._model},
            )
            raise GeminiResponseError("Gemini returned no usable content.")

        logger.info(
            "Gemini request completed",
            extra={
                "event": "gemini_request_completed",
                "model": self._model,
                "function_call_count": len(function_calls),
            },
        )
        return GeminiResult(text=text, function_calls=function_calls)

    def answer(self, question: str) -> str:
        result = self.generate(question)
        if result.text is None:
            raise GeminiResponseError("Gemini requested a function call without text.")
        return result.text

    def close(self) -> None:
        self._client.close()
