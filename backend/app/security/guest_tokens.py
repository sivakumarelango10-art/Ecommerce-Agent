from datetime import datetime, timedelta, timezone
import hashlib
from typing import Any
from uuid import UUID

import jwt

from backend.app.core.config import Settings

GUEST_TOKEN_ALGORITHM = "HS256"
GUEST_TOKEN_TYPE = "guest_order"


def compute_jti_hash(jti: str) -> str:
    """Compute a deterministic SHA-256 digest of the token's JTI."""
    return hashlib.sha256(jti.encode("utf-8")).hexdigest()


def create_guest_access_token(
    order_id: UUID,
    order_number: str,
    email: str,
    jti: str,
    settings: Settings,
    expires_in_minutes: int | None = None,
) -> tuple[str, datetime]:
    """Issue a restricted HS256 JWT specifically scoped to a single verified guest order."""
    now = datetime.now(timezone.utc)
    lifetime = expires_in_minutes or settings.guest_token_expire_minutes
    exp = now + timedelta(minutes=lifetime)

    payload = {
        "sub": str(order_id),
        "type": GUEST_TOKEN_TYPE,
        "order_id": str(order_id),
        "order_number": order_number,
        "email": email,
        "jti": jti,
        "iat": now,
        "exp": exp,
    }

    token = jwt.encode(
        payload,
        settings.auth_secret_key.get_secret_value(),
        algorithm=GUEST_TOKEN_ALGORITHM,
    )
    return token, exp


def decode_guest_access_token(token: str, settings: Settings) -> dict[str, Any]:
    """Decode and validate a restricted guest token.

    Rejects tokens with mismatched type, invalid signature, or missing required claims.
    """
    payload = jwt.decode(
        token,
        settings.auth_secret_key.get_secret_value(),
        algorithms=[GUEST_TOKEN_ALGORITHM],
        options={
            "require": ["exp", "type", "order_id", "order_number", "email", "jti"]
        },
    )

    if payload.get("type") != GUEST_TOKEN_TYPE:
        raise jwt.InvalidTokenError(
            f"Expected token type '{GUEST_TOKEN_TYPE}', got '{payload.get('type')}'"
        )

    return payload
