from __future__ import annotations

from dataclasses import dataclass
import logging
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from backend.app.core.datetime_utils import utc_now
from backend.app.db.models import GeminiUsageEvent

logger = logging.getLogger(__name__)

# Telemetry source values representing company/platform-managed credentials.
COMPANY_KEY_SOURCES: tuple[str, ...] = ("COMPANY", "PLATFORM_KEY")


@dataclass(frozen=True)
class NormalizedTelemetryRecord:
    """Standardized provider usage telemetry record.

    Matches the durable telemetry contract across all AI providers.
    Never holds raw prompts, credentials, or unredacted model responses.
    """

    correlation_id: str
    provider: str
    model_name: str
    key_source: str  # e.g. "PERSONAL", "COMPANY", "PLATFORM_KEY"
    status: str  # "SUCCESS", "FAILED", "COOLDOWN", "FALLBACK"
    user_id: UUID | None = None
    key_slot: str | None = None
    attempt_number: int = 1
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    error_category: str | None = None
    fallback_reason: str | None = None
    latency_ms: float | None = None


def record_normalized_telemetry(
    session: Session,
    record: NormalizedTelemetryRecord,
) -> None:
    """Persist normalized telemetry record into the database.

    Enforces:
    - Never fabricates token counts if None.
    - Never stores API keys or raw prompts.
    - Swallows database persistence errors safely so telemetry issues never break AI responses.
    """
    try:
        event = GeminiUsageEvent(
            id=uuid4(),
            correlation_id=record.correlation_id,
            user_id=record.user_id,
            provider=record.provider,
            model_name=record.model_name,
            key_source=record.key_source,
            key_slot=record.key_slot,
            attempt_number=record.attempt_number,
            status=record.status,
            input_tokens=record.input_tokens,
            output_tokens=record.output_tokens,
            total_tokens=record.total_tokens,
            error_category=record.error_category,
            fallback_reason=record.fallback_reason,
            latency_ms=record.latency_ms,
            created_at=utc_now(),
        )
        session.add(event)
        session.commit()
    except Exception as exc:
        logger.warning(
            "Failed to record normalized provider usage telemetry event",
            extra={"event": "telemetry_persistence_failed", "error_type": type(exc).__name__},
        )
        try:
            session.rollback()
        except Exception:
            pass
