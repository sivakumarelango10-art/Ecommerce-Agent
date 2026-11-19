from datetime import datetime, timedelta, timezone
from uuid import UUID

import jwt

from backend.app.core.config import Settings

ALGORITHM = "HS256"


def create_access_token(user_id: UUID, settings: Settings) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_expire_minutes),
        "type": "access",
    }
    return jwt.encode(payload, settings.auth_secret_key.get_secret_value(), algorithm=ALGORITHM)


def decode_access_token(token: str, settings: Settings) -> UUID:
    payload = jwt.decode(
        token,
        settings.auth_secret_key.get_secret_value(),
        algorithms=[ALGORITHM],
        options={"require": ["exp", "sub", "type"]},
    )
    if payload["type"] != "access":
        raise jwt.InvalidTokenError("Unsupported token type")
    return UUID(payload["sub"])
