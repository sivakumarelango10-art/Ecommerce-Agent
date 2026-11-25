import logging
import math
import re
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx
from google import genai
from google.genai import errors, types
from pydantic import SecretStr

from backend.app.core.config import Settings
from backend.app.core.constants import MAX_QUESTION_LENGTH

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GeminiResult:
    text: str | None
    function_calls: tuple[types.FunctionCall, ...]
    candidate_content: types.Content | None = None
    usage_metadata: Any | None = None


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


def _mask_key(api_key: str) -> str:
    clean = api_key.strip()
    if len(clean) > 4:
        return f"...{clean[-4:]}"
    return "****"


def _parse_duration(val: Any) -> float | None:
    """
    Parse retry duration from string ('65s', '65.5s'), numeric values,
    dictionaries ({'seconds': 60, 'nanos': 500000000}), or protobuf Duration objects.
    Returns positive finite float seconds, or None if invalid/non-positive.
    """
    if val is None:
        return None

    try:
        # 1. Numeric (int or float)
        if isinstance(val, (int, float)):
            delay = float(val)
            if math.isfinite(delay) and delay > 0:
                return delay
            return None

        # 2. String representation (e.g. "65s", "65.5s", "65")
        if isinstance(val, str):
            clean = val.strip().lower()
            if clean.endswith("s"):
                clean = clean[:-1].strip()
            delay = float(clean)
            if math.isfinite(delay) and delay > 0:
                return delay
            return None

        # 3. Dictionary representation (e.g. {"seconds": 60, "nanos": 500000000})
        if isinstance(val, dict) and ("seconds" in val or "nanos" in val):
            sec = float(val.get("seconds", 0) or 0)
            nanos = float(val.get("nanos", 0) or 0)
            delay = sec + (nanos / 1_000_000_000.0)
            if math.isfinite(delay) and delay > 0:
                return delay
            return None

        # 4. Protobuf Duration or timedelta object with total_seconds() or seconds/nanos attributes
        if hasattr(val, "total_seconds") and callable(val.total_seconds):
            delay = float(val.total_seconds())
            if math.isfinite(delay) and delay > 0:
                return delay
            return None

        if hasattr(val, "seconds") or hasattr(val, "nanos"):
            sec = float(getattr(val, "seconds", 0) or 0)
            nanos = float(getattr(val, "nanos", 0) or 0)
            delay = sec + (nanos / 1_000_000_000.0)
            if math.isfinite(delay) and delay > 0:
                return delay
            return None

    except (ValueError, TypeError, AttributeError, OverflowError):
        return None

    return None


def _is_daily_quota_exhaustion(details: Any, message: str) -> bool:
    """
    Determine whether a 429 quota exhaustion is due to a daily quota limit (RPD).
    Inspects structured QuotaFailure and ErrorInfo metadata before falling back
    to message pattern matching. Distinguishes RPM and TPM from RPD.
    """
    # 1. Inspect structured details
    if details:
        def inspect_obj(obj: Any) -> bool | None:
            if isinstance(obj, dict):
                # Check for google.rpc.QuotaFailure violations
                if "violations" in obj and isinstance(obj["violations"], list):
                    for v in obj["violations"]:
                        if isinstance(v, dict):
                            metric_fields = [
                                str(v.get("metric_name") or "").lower(),
                                str(v.get("quota_metric") or "").lower(),
                                str(v.get("quota_limit") or "").lower(),
                                str(v.get("description") or "").lower(),
                            ]
                            combined = " ".join(metric_fields)
                            if any(d in combined for d in ("per_day", "per-day", "per day", "requests per day", "queries per day", "rpd")):
                                return True
                            if any(m in combined for m in ("per_minute", "per-minute", "per minute", "requests per minute", "tokens per minute", "rpm", "tpm")):
                                return False

                # Check for google.rpc.ErrorInfo metadata
                if "metadata" in obj and isinstance(obj["metadata"], dict):
                    meta = obj["metadata"]
                    metric_fields = [
                        str(meta.get("quota_metric") or "").lower(),
                        str(meta.get("quota_limit") or "").lower(),
                        str(meta.get("quota_limit_name") or "").lower(),
                    ]
                    combined = " ".join(metric_fields)
                    if any(d in combined for d in ("per_day", "per-day", "per day", "requests per day", "queries per day", "rpd")):
                        return True
                    if any(m in combined for m in ("per_minute", "per-minute", "per minute", "requests per minute", "tokens per minute", "rpm", "tpm")):
                        return False

                for val in obj.values():
                    res = inspect_obj(val)
                    if res is not None:
                        return res
            elif isinstance(obj, (list, tuple)):
                for item in obj:
                    res = inspect_obj(item)
                    if res is not None:
                        return res
            return None

        result = inspect_obj(details)
        if result is not None:
            return result

    # 2. Message fallback when structured metadata is absent or uninformative
    msg = message.lower()
    daily_patterns = [
        "requests per day",
        "queries per day",
        "tokens per day",
        "daily requests",
        "daily quota",
        "daily limit",
        "exceeded your daily",
        "per-day quota",
        "per-day request",
        "free_tier_daily",
    ]
    minute_patterns = [
        "requests per minute",
        "queries per minute",
        "tokens per minute",
        "per minute",
        "per-minute",
    ]

    has_daily = any(pat in msg for pat in daily_patterns)
    has_minute = any(pat in msg for pat in minute_patterns)

    if has_daily and not has_minute:
        return True
    if has_minute and not has_daily:
        return False
    if has_daily and has_minute:
        return "exceeded your daily" in msg or "daily requests limit" in msg or "requests per day" in msg

    return False


def _extract_cooldown_seconds(exc: errors.APIError) -> tuple[float, bool]:
    """
    Extract structured cooldown information from a Gemini 429 APIError.
    Returns (cooldown_seconds, is_daily).
    """
    message = (getattr(exc, "message", None) or str(exc) or "").lower()
    details = getattr(exc, "details", None)

    is_daily = _is_daily_quota_exhaustion(details, message)
    extracted_delay: float | None = None

    # 1. Check HTTP response headers (Retry-After)
    resp = getattr(exc, "response", None)
    if resp is not None:
        headers = getattr(resp, "headers", {})
        retry_after = headers.get("retry-after") or headers.get("Retry-After")
        if retry_after is not None:
            extracted_delay = _parse_duration(retry_after)

    # 2. Check structured details for RetryInfo, retryDelay, or duration objects
    if extracted_delay is None and details:
        def search_retry_info(obj: Any) -> float | None:
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if k in ("retry_delay", "retryDelay", "retry_after", "retryAfter") and v is not None:
                        parsed = _parse_duration(v)
                        if parsed is not None:
                            return parsed
                    res = search_retry_info(v)
                    if res is not None:
                        return res
            elif isinstance(obj, (list, tuple)):
                for item in obj:
                    res = search_retry_info(item)
                    if res is not None:
                        return res
            elif hasattr(obj, "retry_delay") or hasattr(obj, "retryDelay"):
                attr_val = getattr(obj, "retry_delay", None) or getattr(obj, "retryDelay", None)
                parsed = _parse_duration(attr_val)
                if parsed is not None:
                    return parsed
            return None

        extracted_delay = search_retry_info(details)

    # 3. Fallback search in message string for retry after seconds
    if extracted_delay is None:
        match = re.search(r"retry\s+(?:after|in)\s+([0-9]+(?:\.[0-9]+)?)\s*s?", message)
        if match:
            extracted_delay = _parse_duration(match.group(1))

    # 4. Determine final cooldown duration
    # Rule: If Google supplies a valid positive retry duration, use that exact duration.
    # Do not impose an artificial 1-hour floor on explicit durations.
    if extracted_delay is not None and extracted_delay > 0:
        cooldown_seconds = extracted_delay
    elif is_daily:
        cooldown_seconds = 86400.0  # 24 hours fallback for daily limit
    else:
        cooldown_seconds = 60.0  # 60 seconds fallback for RPM/TPM

    return cooldown_seconds, is_daily


def _is_credential_invalid_error(exc: errors.APIError) -> bool:
    """
    Inspect structured error reasons to determine if an individual API key is invalid/revoked.
    Does NOT classify unrelated model, permission, billing, or request errors as invalid keys.
    """
    if getattr(exc, "code", None) not in (400, 403):
        return False

    message = (getattr(exc, "message", None) or str(exc) or "").lower()
    details = getattr(exc, "details", None)

    # Check known invalid key error reasons in details
    invalid_reasons = {
        "API_KEY_INVALID",
        "API_KEY_EXPIRED",
        "API_KEY_REVOKED",
        "CONSUMER_INVALID",
        "CREDENTIAL_INVALID",
    }

    if details:
        def check_reasons(obj: Any) -> bool:
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if k == "reason" and str(v).upper() in invalid_reasons:
                        return True
                    if check_reasons(v):
                        return True
            elif isinstance(obj, (list, tuple)):
                for item in obj:
                    if check_reasons(item):
                        return True
            return False

        if check_reasons(details):
            return True

    invalid_message_patterns = [
        "api key not valid",
        "api_key_invalid",
        "invalid api key",
        "api key expired",
        "api key revoked",
        "api key deleted",
        "bad api key",
    ]
    return any(pat in message for pat in invalid_message_patterns)


@dataclass
class PlatformKeySlot:
    index: int
    key_id: str
    masked_key: str
    quota_group: str
    client: genai.Client
    is_active: bool = True
    last_used_at: float = 0.0
    failure_count: int = 0


class GeminiService:
    """
    Centralized multi-key Gemini service supporting platform-managed key pooling,
    quota-group cooldowns, automatic failover, and thread-safe selection.

    MULTI-WORKER BEHAVIOR NOTE:
    In-memory cooldown and health tracking is local to each application worker process
    (e.g., each Uvicorn/Gunicorn worker process on Render maintains its own pool state).
    State is managed thread-safely per process using a threading mutex lock, without requiring Redis
    or external infrastructure. Cooldown state is not globally shared across separate
    OS processes or instances.
    """

    def __init__(
        self,
        settings: Settings,
        client_factory: Callable[..., genai.Client] | None = None,
    ) -> None:
        self._model = settings.gemini_model
        self._timeout_seconds = settings.gemini_timeout_seconds
        self._lock = threading.Lock()
        self._quota_group_cooldowns: dict[str, float] = {}
        self._rr_counter = 0

        http_options = types.HttpOptions(
            timeout=settings.gemini_timeout_seconds * 1000,
            retry_options=types.HttpRetryOptions(
                attempts=4,
                initial_delay=1.0,
                max_delay=4.0,
                http_status_codes=[408, 429, 500, 502, 503, 504],
            ),
        )

        factory = client_factory or genai.Client
        self._keys: list[PlatformKeySlot] = []

        for idx, key_cfg in enumerate(settings.platform_gemini_keys):
            raw_key = key_cfg.key.get_secret_value()
            masked = _mask_key(raw_key)
            key_id = f"slot_{idx}:{key_cfg.quota_group}:{masked}"
            client = factory(api_key=raw_key, http_options=http_options)
            self._keys.append(
                PlatformKeySlot(
                    index=idx,
                    key_id=key_id,
                    masked_key=masked,
                    quota_group=key_cfg.quota_group,
                    client=client,
                )
            )

        if not self._keys:
            raise ValueError("No Gemini API keys configured.")

    @property
    def _client(self) -> genai.Client:
        """Backward-compatibility property returning the primary client."""
        if self._keys:
            return self._keys[0].client
        raise GeminiProviderError("No Gemini clients configured.")

    @property
    def _clients(self) -> list[genai.Client]:
        """Returns all configured clients."""
        return [k.client for k in self._keys]

    def _select_eligible_key(
        self,
        attempted_indices: set[int],
    ) -> PlatformKeySlot | None:
        with self._lock:
            now = time.time()
            eligible = [
                slot
                for slot in self._keys
                if slot.is_active
                and slot.index not in attempted_indices
                and self._quota_group_cooldowns.get(slot.quota_group, 0.0) <= now
            ]
            if not eligible:
                return None

            selected = eligible[self._rr_counter % len(eligible)]
            self._rr_counter = (self._rr_counter + 1) % len(self._keys)
            selected.last_used_at = now
            return selected

    def _check_pool_failure_reason(self) -> str:
        with self._lock:
            now = time.time()
            active_slots = [s for s in self._keys if s.is_active]
            if not active_slots:
                return "ALL_KEYS_DISABLED"
            active_groups = {s.quota_group for s in active_slots}
            if all(self._quota_group_cooldowns.get(g, 0.0) > now for g in active_groups):
                return "ALL_GROUPS_COOLDOWN"
            return "EXHAUSTED_ATTEMPTS"

    def get_platform_key_health(self) -> list[dict[str, Any]]:
        """Safe thread-safe snapshot of platform key health, cooldowns and slots.

        Never exposes raw API keys, SecretStr, or environment credentials.
        """
        with self._lock:
            now = time.time()
            result = []
            for slot in self._keys:
                cooldown_until = self._quota_group_cooldowns.get(slot.quota_group, 0.0)
                in_cooldown = cooldown_until > now
                remaining = max(0.0, cooldown_until - now) if in_cooldown else 0.0
                if not slot.is_active:
                    health = "DISABLED"
                elif in_cooldown:
                    health = "COOLDOWN"
                else:
                    health = "HEALTHY"
                last_used = (
                    datetime.fromtimestamp(slot.last_used_at, tz=timezone.utc)
                    if slot.last_used_at > 0
                    else None
                )
                result.append({
                    "slot_index": slot.index,
                    "slot_id": f"slot_{slot.index}",
                    "key_id": slot.key_id,
                    "masked_key": slot.masked_key,
                    "quota_group": slot.quota_group,
                    "is_active": slot.is_active,
                    "in_cooldown": in_cooldown,
                    "cooldown_remaining_seconds": round(remaining, 1),
                    "health_status": health,
                    "failure_count": slot.failure_count,
                    "last_used_at": last_used,
                })
            return result

    def generate(
        self,
        contents: str | Sequence[types.Content] | None = None,
        *,
        question: str | None = None,
        tools: Sequence[types.Tool] | None = None,
        system_instruction: str | None = None,
        user_key: SecretStr | None = None,
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

        attempted_indices: set[int] = set()
        attempted_quota_groups: set[str] = set()
        transient_attempts = 0
        max_transient_attempts = min(len(self._keys), 3)
        last_error: Exception | None = None
        response = None

        while True:
            slot = self._select_eligible_key(attempted_indices)
            if slot is None:
                pool_status = self._check_pool_failure_reason()
                if pool_status == "ALL_KEYS_DISABLED":
                    raise GeminiProviderError(
                        "All Gemini API keys are permanently disabled due to invalid credentials."
                    )
                if pool_status == "ALL_GROUPS_COOLDOWN" or len(attempted_quota_groups) >= len(
                    {k.quota_group for k in self._keys if k.is_active}
                ):
                    raise GeminiProviderError(
                        "Gemini API quota exhausted across all available project groups."
                    )
                if last_error is not None:
                    if isinstance(last_error, (httpx.TimeoutException, TimeoutError)):
                        raise GeminiTimeoutError("Gemini request timed out.") from last_error
                    if isinstance(last_error, errors.APIError):
                        raise GeminiProviderError("Gemini request failed.") from last_error
                    raise GeminiProviderError("Gemini request failed.") from last_error
                raise GeminiProviderError("Gemini request failed.")

            attempted_indices.add(slot.index)
            attempted_quota_groups.add(slot.quota_group)

            try:
                response = slot.client.models.generate_content(
                    model=self._model,
                    contents=payload_contents,
                    config=config,
                )
                slot.failure_count = 0
                break
            except (httpx.TimeoutException, TimeoutError) as exc:
                last_error = exc
                slot.failure_count += 1
                transient_attempts += 1
                logger.warning(
                    "Gemini request timed out on key %s (group: %s)",
                    slot.key_id,
                    slot.quota_group,
                    extra={
                        "event": "gemini_timeout_failover",
                        "key_id": slot.key_id,
                        "quota_group": slot.quota_group,
                    },
                )
                if transient_attempts >= max_transient_attempts:
                    raise GeminiTimeoutError("Gemini request timed out.") from exc
                continue
            except httpx.RequestError as exc:
                last_error = exc
                slot.failure_count += 1
                transient_attempts += 1
                logger.warning(
                    "Gemini transport error on key %s (group: %s)",
                    slot.key_id,
                    slot.quota_group,
                    extra={
                        "event": "gemini_transport_failover",
                        "key_id": slot.key_id,
                        "quota_group": slot.quota_group,
                        "error_type": type(exc).__name__,
                    },
                )
                if transient_attempts >= max_transient_attempts:
                    raise GeminiProviderError("Gemini request failed.") from exc
                continue
            except errors.APIError as exc:
                last_error = exc
                # 1. Quota / Rate limit (429 / RESOURCE_EXHAUSTED)
                if exc.code == 429 or getattr(exc, "status", None) == "RESOURCE_EXHAUSTED":
                    cooldown_secs, is_daily = _extract_cooldown_seconds(exc)
                    with self._lock:
                        now = time.time()
                        self._quota_group_cooldowns[slot.quota_group] = max(
                            self._quota_group_cooldowns.get(slot.quota_group, 0.0),
                            now + cooldown_secs,
                        )
                    logger.warning(
                        "Gemini 429 quota/rate limit encountered on quota group '%s' (cooldown: %.1fs, daily: %s).",
                        slot.quota_group,
                        cooldown_secs,
                        is_daily,
                        extra={
                            "event": "gemini_quota_exhausted",
                            "quota_group": slot.quota_group,
                            "cooldown_seconds": cooldown_secs,
                            "is_daily": is_daily,
                        },
                    )
                    continue

                # 2. Invalid or revoked credentials (400 / 403 API key invalid)
                if exc.code in (400, 403) and _is_credential_invalid_error(exc):
                    with self._lock:
                        slot.is_active = False
                    logger.error(
                        "Gemini API key %s disabled due to invalid credentials",
                        slot.key_id,
                        extra={
                            "event": "gemini_key_disabled",
                            "key_id": slot.key_id,
                            "quota_group": slot.quota_group,
                        },
                    )
                    continue

                # 3. Server errors (500, 502, 503, 504)
                if exc.code in (500, 502, 503, 504):
                    slot.failure_count += 1
                    transient_attempts += 1
                    logger.warning(
                        "Gemini server error %s on key %s (group: %s). Failing over.",
                        exc.code,
                        slot.key_id,
                        slot.quota_group,
                        extra={
                            "event": "gemini_server_error_failover",
                            "code": exc.code,
                            "key_id": slot.key_id,
                        },
                    )
                    if transient_attempts >= max_transient_attempts:
                        raise GeminiProviderError("Gemini request failed.") from exc
                    continue

                # 4. Other API errors (unrelated 400/403, bad request, model not found, etc.)
                logger.error(
                    "Gemini API request failed",
                    extra={
                        "event": "gemini_api_error",
                        "model": self._model,
                        "status_code": exc.code,
                    },
                )
                raise GeminiProviderError("Gemini request failed.") from exc

        if response is None:
            raise GeminiProviderError("Gemini request failed.")

        response_text = getattr(response, "text", None)
        text = (
            response_text.strip()
            if isinstance(response_text, str) and response_text.strip()
            else None
        )
        function_calls = tuple(getattr(response, "function_calls", None) or ())
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
            usage_metadata=getattr(response, "usage_metadata", None),
        )

    def answer(
        self,
        question: str,
        *,
        user_key: SecretStr | None = None,
    ) -> str:
        result = self.generate(question, user_key=user_key)
        if result.text is None:
            raise GeminiResponseError("Gemini requested a function call without text.")
        return result.text

    def close(self) -> None:
        with self._lock:
            for slot in self._keys:
                try:
                    slot.client.close()
                except Exception:
                    logger.warning(
                        "Error closing Gemini client for key %s",
                        slot.key_id,
                        extra={"event": "gemini_client_close_error", "key_id": slot.key_id},
                    )
