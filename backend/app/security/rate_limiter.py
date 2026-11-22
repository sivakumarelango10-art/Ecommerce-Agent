from datetime import datetime, timedelta, timezone
import hashlib
import logging
from typing import Literal
from uuid import UUID, uuid4

from fastapi import HTTPException, Request, status
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from backend.app.core.config import Settings
from backend.app.core.datetime_utils import ensure_utc, utc_now
from backend.app.db.models import AuthRateLimit

logger = logging.getLogger(__name__)


def extract_client_ip(request: Request) -> str:
    """Extract client IP, checking trusted upstream proxy headers first."""
    cf_connecting_ip = request.headers.get("cf-connecting-ip")
    if cf_connecting_ip:
        return cf_connecting_ip.strip()

    x_forwarded_for = request.headers.get("x-forwarded-for")
    if x_forwarded_for:
        parts = x_forwarded_for.split(",")
        if parts and parts[0].strip():
            return parts[0].strip()

    x_real_ip = request.headers.get("x-real-ip")
    if x_real_ip:
        return x_real_ip.strip()

    if request.client and request.client.host:
        return request.client.host.strip()

    return "127.0.0.1"


def compute_rate_limit_hash(value: str) -> str:
    """Compute deterministic SHA-256 hash to protect user/IP privacy in rate limit logs/tables."""
    return hashlib.sha256(value.strip().lower().encode("utf-8")).hexdigest()


def check_and_record_auth_rate_limit(
    session: Session,
    request: Request,
    action: Literal["login", "register"],
    identifier: str | None = None,
    settings: Settings | None = None,
) -> None:
    """Evaluate and record authentication attempts against distributed database storage.

    - Protects against brute-force attacks and credential stuffing across multiple app workers.
    - Never stores plaintext passwords, raw IPs, or sensitive request bodies.
    - Enforces upstream WAF/proxy rate limit headers if present.
    - Prevents account enumeration by evaluating limits before checking account existence.
    """
    # 1. Upstream Reverse-Proxy / WAF Header Enforcement
    if (
        request.headers.get("x-ratelimit-exceeded", "").lower() == "true"
        or request.headers.get("x-waf-ratelimited", "").lower() == "true"
    ):
        retry_after = request.headers.get("retry-after", "60")
        try:
            retry_int = int(retry_after)
        except ValueError:
            retry_int = 60
        logger.warning(
            "Upstream WAF/Proxy rate limit triggered",
            extra={"event": "waf_rate_limit_exceeded", "action": action},
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many authentication requests. Blocked by upstream rate limiter.",
            headers={"Retry-After": str(max(1, retry_int))},
        )

    # 2. Resolve thresholds
    limit_per_minute = 10 if action == "login" else 5
    window_seconds = 60
    if settings:
        if action == "login":
            limit_per_minute = settings.auth_rate_limit_login_per_minute
        else:
            limit_per_minute = settings.auth_rate_limit_register_per_minute
        window_seconds = settings.auth_rate_limit_window_seconds

    client_ip = extract_client_ip(request)
    ip_key = compute_rate_limit_hash(f"ip:{client_ip}:{action}")

    keys_to_check = [(ip_key, limit_per_minute)]

    # If target email provided, evaluate target-specific limit (max attempts per email)
    if identifier:
        target_key = compute_rate_limit_hash(f"target:{identifier}:{action}")
        keys_to_check.append((target_key, limit_per_minute))

    now = utc_now()
    window_start = now - timedelta(seconds=window_seconds)

    # 3. Check existing counts in database
    for key_hash, max_limit in keys_to_check:
        count_stmt = select(func.count(AuthRateLimit.id)).where(
            AuthRateLimit.key_hash == key_hash,
            AuthRateLimit.action == action,
            AuthRateLimit.created_at >= window_start,
        )
        current_count = session.scalar(count_stmt) or 0
        if current_count >= max_limit:
            # Find earliest attempt in current window to calculate exact remaining cooldown
            earliest_stmt = (
                select(AuthRateLimit.created_at)
                .where(
                    AuthRateLimit.key_hash == key_hash,
                    AuthRateLimit.action == action,
                    AuthRateLimit.created_at >= window_start,
                )
                .order_by(AuthRateLimit.created_at.asc())
                .limit(1)
            )
            earliest_created = session.scalar(earliest_stmt)
            earliest_utc = ensure_utc(earliest_created) if earliest_created else window_start
            remaining = int(
                (earliest_utc + timedelta(seconds=window_seconds) - now).total_seconds()
            )
            retry_after = max(1, remaining)

            logger.warning(
                "Authentication rate limit exceeded",
                extra={
                    "event": "auth_rate_limit_exceeded",
                    "action": action,
                    "retry_after": retry_after,
                },
            )
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many authentication attempts. Please try again later.",
                headers={"Retry-After": str(retry_after)},
            )

    # 4. Record new attempt into distributed database storage
    expires_at = now + timedelta(seconds=window_seconds * 2)
    session.add(
        AuthRateLimit(
            id=uuid4(),
            key_hash=ip_key,
            action=action,
            created_at=now,
            expires_at=expires_at,
        )
    )
    if identifier:
        target_key = compute_rate_limit_hash(f"target:{identifier}:{action}")
        session.add(
            AuthRateLimit(
                id=uuid4(),
                key_hash=target_key,
                action=action,
                created_at=now,
                expires_at=expires_at,
            )
        )

    # Opportunistic cleanup of expired rate limit entries
    session.execute(
        delete(AuthRateLimit).where(AuthRateLimit.expires_at < now)
    )
    session.commit()
