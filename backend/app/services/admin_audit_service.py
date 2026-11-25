from __future__ import annotations

import logging
import re
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from backend.app.core.datetime_utils import utc_now
from backend.app.db.models import AdminAuditLog

logger = logging.getLogger(__name__)

# Sensitive keys that must NEVER be persisted in audit log metadata
_SENSITIVE_KEY_PATTERN = re.compile(
    r"(?:password|secret|api_?key|token|auth|ciphertext|prompt|credential|private)",
    re.IGNORECASE,
)

# Explicitly allowed safe metadata keys that might match the key pattern (e.g. key_hint, key_source)
_SAFE_KEY_EXCEPTIONS = {"key_hint", "key_source", "key_slot", "status", "role"}

# Targeted patterns for credential scanning in arbitrary string values
_SECRET_VALUE_PATTERNS = [
    # Google Gemini API key: 'AIza' followed by 30+ chars
    re.compile(r"\bAIza[0-9A-Za-z\-_]{30,}\b"),
    # Synthetic test keys for safe unit test verification without real credentials
    re.compile(r"\bFAKE_API_KEY_[a-zA-Z0-9_\-]{10,}\b"),
    # Provider secret keys (sk-...)
    re.compile(r"\bsk-[a-zA-Z0-9]{20,}\b"),
    # Anthropic API keys
    re.compile(r"\bsk-ant-[a-zA-Z0-9]{20,}\b"),
    # Bearer authorization tokens: 'Bearer <token>'
    re.compile(r"(?i)\bbearer\s+[a-zA-Z0-9_\-\.=~+/]{10,}\b"),
    # JWT tokens: 'eyJ...' (full three-segment or raw base64 header/token)
    re.compile(r"\beyJ[a-zA-Z0-9_\-]{10,}(?:\.[a-zA-Z0-9_\-]{10,}\.[a-zA-Z0-9_\-]+)?\b"),
    # Argon2 password hashes
    re.compile(r"\$argon2(?:id|i|d)\$[^\s]+"),
    # StoreFlow Fernet ciphertext tokens: 'v1:gAAAAA...'
    re.compile(r"\bv[0-9]+:gAAAAA[a-zA-Z0-9_\-=]{20,}\b"),
]


def sanitize_string_value(val: str, max_len: int = 1000) -> str:
    """Scan string for targeted credentials and redact them while preserving ordinary text."""
    cleaned = val
    for pat in _SECRET_VALUE_PATTERNS:
        cleaned = pat.sub("[REDACTED_SECRET]", cleaned)
    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len] + "...[TRUNCATED]"
    return cleaned


def sanitize_audit_metadata(data: dict[str, Any] | None) -> dict[str, Any] | None:
    """Recursively scrub sensitive keys, passwords, credentials, and prompts from audit metadata.

    - Performs key-based redaction for sensitive dictionary keys.
    - Performs value-based targeted regex scanning on string values for API keys, JWTs, and credentials.
    - Preserves safe fields (e.g. key_hint, key_source, status, role).
    - Preserves surrounding ordinary descriptive text.
    """
    if data is None:
        return None

    scrubbed: dict[str, Any] = {}
    for k, v in data.items():
        key_str = str(k).strip()
        lower_key = key_str.lower()

        # 1. Key-level redaction
        if _SENSITIVE_KEY_PATTERN.search(lower_key) and lower_key not in _SAFE_KEY_EXCEPTIONS:
            scrubbed[key_str] = "[REDACTED]"
            continue

        # 2. Value-level inspection and recursion
        if isinstance(v, dict):
            scrubbed[key_str] = sanitize_audit_metadata(v)
        elif isinstance(v, (list, tuple)):
            cleaned_list: list[Any] = []
            for item in v:
                if isinstance(item, dict):
                    cleaned_list.append(sanitize_audit_metadata(item))
                elif isinstance(item, (list, tuple)):
                    cleaned_list.append(
                        [
                            sanitize_audit_metadata(x)
                            if isinstance(x, dict)
                            else (sanitize_string_value(x, max_len=500) if isinstance(x, str) else x)
                            for x in item
                        ]
                    )
                elif isinstance(item, str):
                    cleaned_list.append(sanitize_string_value(item, max_len=500))
                else:
                    cleaned_list.append(item)
            scrubbed[key_str] = cleaned_list
        elif isinstance(v, str):
            scrubbed[key_str] = sanitize_string_value(v, max_len=1000)
        else:
            scrubbed[key_str] = v

    return scrubbed


def record_admin_audit_event(
    session: Session,
    *,
    actor_user_id: UUID | None,
    action: str,
    target_type: str,
    target_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> AdminAuditLog:
    """Record an immutable administrative audit event.

    - Sanitizes metadata to strictly strip sensitive information and credentials.
    - Safely bounds target_id, action, target_type, ip_address, and user_agent lengths.
    - Adds the log entry to the active SQLAlchemy session.
    """
    clean_metadata = sanitize_audit_metadata(metadata)
    clean_action = action.strip()[:64]
    clean_target_type = target_type.strip()[:64]
    clean_target_id = str(target_id).strip()[:128] if target_id is not None else None
    clean_ip = ip_address[:45] if ip_address else None
    clean_ua = user_agent[:512] if user_agent else None

    entry = AdminAuditLog(
        id=uuid4(),
        actor_user_id=actor_user_id,
        action=clean_action,
        target_type=clean_target_type,
        target_id=clean_target_id,
        metadata_json=clean_metadata,
        ip_address=clean_ip,
        user_agent=clean_ua,
        created_at=utc_now(),
    )
    session.add(entry)
    return entry
