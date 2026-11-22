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
    candidate_content: types.Content | None = None


_SYSTEM_INSTRUCTION = (
    "You are an e-commerce assistant for general product and shopping questions. "
    "You have no access to customer accounts, orders, payments, or inventory. "
    "Do not claim to retrieve or change private customer data or complete transactions. "
    "Store policy information (including return periods, refund policies, shipping timelines, "
    "and warranty terms) is not currently established or available. If asked about store policies, "
    "returns, refunds, shipping, delivery, or warranties, you must state that verified store policy "
    "information is not currently available and direct the customer to contact customer support. "
    "Never invent or assume return windows, shipping rates, warranty durations, or store rules."
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
        contents: str | Sequence[types.Content] | None = None,
        *,
        question: str | None = None,
        tools: Sequence[types.Tool] | None = None,
        system_instruction: str | None = None,
    ) -> GeminiResult:
        if contents is None:
            if question is not None:
                contents = question
            else:
                raise ValueError("question must not be blank")

        if isinstance(contents, str):
            contents_str = contents.strip()
            if not contents_str:
                raise ValueError("question must not be blank")
            if len(contents_str) > MAX_QUESTION_LENGTH:
                raise ValueError(
                    f"question must not exceed {MAX_QUESTION_LENGTH} characters"
                )
            payload_contents: str | list[types.Content] = contents_str
        elif isinstance(contents, Sequence):
            if not contents:
                raise ValueError("contents must not be empty")
            payload_contents = list(contents)
        else:
            payload_contents = contents

        effective_instruction = (
            system_instruction
            if system_instruction is not None
            else _SYSTEM_INSTRUCTION
        )

        config = types.GenerateContentConfig(
            system_instruction=effective_instruction,
            tools=list(tools) if tools else None,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(
                disable=True
            ),
        )

        try:
            response = self._client.models.generate_content(
                model=self._model,
                contents=payload_contents,
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

        candidate_content: types.Content | None = None
        if getattr(response, "candidates", None) and len(response.candidates) > 0:
            candidate_content = getattr(response.candidates[0], "content", None)
        if candidate_content is None and function_calls:
            candidate_content = types.Content(
                role="model",
                parts=[
                    types.Part.from_function_call(name=fc.name, args=dict(fc.args or {}))
                    for fc in function_calls
                ],
            )
        elif candidate_content is None and text:
            candidate_content = types.Content(
                role="model",
                parts=[types.Part.from_text(text=text)],
            )

        logger.info(
            "Gemini request completed",
            extra={
                "event": "gemini_request_completed",
                "model": self._model,
                "function_call_count": len(function_calls),
            },
        )
        return GeminiResult(
            text=text,
            function_calls=function_calls,
            candidate_content=candidate_content,
        )

    def answer(self, question: str) -> str:
        result = self.generate(question)
        if result.text is None:
            raise GeminiResponseError("Gemini requested a function call without text.")
        return result.text

    def close(self) -> None:
        self._client.close()
