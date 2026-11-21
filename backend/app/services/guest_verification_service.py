"""Guest order verification service with secure OTP delivery and restricted session management.

Note on SQLite concurrency limitations:
SQLite does not support row-level locking (SELECT ... FOR UPDATE is a no-op). SQLite relies on
database-level file locks. Full row-level concurrency serialization (FOR UPDATE) is enforced when
running against PostgreSQL.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import logging
import secrets
from typing import Any
from uuid import UUID, uuid4

from pydantic import SecretStr
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from backend.app.core.config import Settings
from backend.app.core.datetime_utils import ensure_utc, utc_now
from backend.app.db.models import (
    GuestAccessSession,
    GuestEmailOutbox,
    GuestOrderVerification,
    Order,
    OutboxDeliveryStatus,
)
from backend.app.security.guest_tokens import (
    compute_jti_hash,
    create_guest_access_token,
)
from backend.app.security.otp_encryption import decrypt_otp, encrypt_otp
from backend.app.services.email_service import (
    EmailDeliveryError,
    EmailDeliveryProvider,
    get_email_provider,
)

logger = logging.getLogger(__name__)

OTP_EXPIRY_MINUTES = 5
MAX_VERIFICATION_ATTEMPTS = 5
RESEND_COOLDOWN_SECONDS = 60
MAX_RESENDS_PER_WINDOW = 3
RATE_LIMIT_WINDOW_MINUTES = 15
MAX_OUTBOX_ATTEMPTS = 3


class GuestVerificationError(Exception):
    """Base error for guest order verification failures."""


class MissingSecretError(GuestVerificationError):
    """Raised when OTP_HASH_SECRET is missing."""


class RateLimitExceededError(GuestVerificationError):
    """Raised when OTP request cooldown or window limits are exceeded."""

    def __init__(self, message: str, retry_after: int) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class InvalidOTPError(GuestVerificationError):
    """Raised when supplied OTP does not match."""


class ExpiredOTPError(GuestVerificationError):
    """Raised when OTP has expired or already been consumed."""


class OTPAttemptsExceededError(GuestVerificationError):
    """Raised when max verification attempts are exhausted."""


def hash_otp(otp: str, secret: SecretStr) -> str:
    """Compute HMAC-SHA256 digest of an OTP using the dedicated pepper secret."""
    key = secret.get_secret_value().encode("utf-8")
    return hmac.new(key, otp.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_otp_hash(candidate_otp: str, stored_hash: str, secret: SecretStr) -> bool:
    """Constant-time verification of candidate OTP against stored HMAC-SHA256 digest."""
    candidate_hash = hash_otp(candidate_otp, secret)
    return hmac.compare_digest(stored_hash, candidate_hash)


def generate_otp() -> str:
    """Generate a uniform 6-digit cryptographically secure numeric OTP."""
    return f"{secrets.randbelow(1_000_000):06d}"


def request_guest_verification(
    session: Session,
    order_number: str,
    email: str,
    settings: Settings,
    email_provider: EmailDeliveryProvider | None = None,
) -> bool:
    """Initiate guest order verification by creating an OTP and outbox job.

    Locks the Order row using SELECT FOR UPDATE in PostgreSQL to strictly serialize concurrent
    verification and resend requests. Dispatches via transactional outbox.
    Always returns cleanly whether order exists or matches to prevent enumeration.
    """
    if not settings.otp_hash_secret:
        logger.error(
            "OTP verification failed closed: OTP_HASH_SECRET is unconfigured",
            extra={"event": "otp_secret_missing"},
        )
        raise MissingSecretError(
            "OTP verification service is currently misconfigured."
        )

    clean_order_number = order_number.strip().upper()
    clean_email = email.strip().lower()

    # Lock the order row using SELECT ... FOR UPDATE in PostgreSQL
    stmt = select(Order).where(Order.order_number == clean_order_number)
    try:
        stmt = stmt.with_for_update()
    except Exception:
        pass

    order = session.scalar(stmt)

    # Prevent timing attack / enumeration: return False for missing order or email mismatch
    if (
        order is None
        or not order.customer_email_snapshot
        or order.customer_email_snapshot.strip().lower() != clean_email
    ):
        logger.info(
            "Guest verification requested for non-matching order/email",
            extra={"event": "guest_verify_nomatch"},
        )
        return False

    now = utc_now()
    window_start = now - timedelta(minutes=RATE_LIMIT_WINDOW_MINUTES)
    cooldown_threshold = now - timedelta(seconds=RESEND_COOLDOWN_SECONDS)

    # Persistence-backed rate limiting & cooldown for this specific order.
    # Exclude verifications where email delivery permanently failed so customers are not locked out.
    recent_verifications = list(
        session.scalars(
            select(GuestOrderVerification)
            .where(
                GuestOrderVerification.order_id == order.id,
                GuestOrderVerification.created_at >= window_start,
                GuestOrderVerification.delivery_failed.is_(False),
            )
            .order_by(GuestOrderVerification.created_at.desc())
        ).all()
    )

    if recent_verifications:
        latest = recent_verifications[0]
        latest_created = ensure_utc(latest.created_at)
        # Check 60-second cooldown
        if latest_created and latest_created > cooldown_threshold:
            remaining = int(
                (
                    latest_created
                    + timedelta(seconds=RESEND_COOLDOWN_SECONDS)
                    - now
                ).total_seconds()
            )
            retry_after = max(1, remaining)
            logger.warning(
                "Guest verification cooldown active",
                extra={"event": "guest_verify_cooldown", "order_id": str(order.id)},
            )
            raise RateLimitExceededError(
                f"Please wait {retry_after} seconds before requesting another verification code.",
                retry_after=retry_after,
            )

        # Check maximum resends in the 15-minute window
        if len(recent_verifications) > MAX_RESENDS_PER_WINDOW:
            logger.warning(
                "Guest verification resend limit exceeded",
                extra={"event": "guest_verify_limit", "order_id": str(order.id)},
            )
            raise RateLimitExceededError(
                "Maximum verification requests exceeded for this order. Please try again later.",
                retry_after=RATE_LIMIT_WINDOW_MINUTES * 60,
            )

    # Invalidate previous unconsumed verification OTPs for this order
    session.execute(
        update(GuestOrderVerification)
        .where(
            GuestOrderVerification.order_id == order.id,
            GuestOrderVerification.consumed_at.is_(None),
        )
        .values(consumed_at=now)
    )

    otp = generate_otp()
    otp_digest = hash_otp(otp, settings.otp_hash_secret)
    encrypted_otp = encrypt_otp(otp, settings.otp_hash_secret)
    resend_count = len(recent_verifications)

    verification_record = GuestOrderVerification(
        id=uuid4(),
        order_id=order.id,
        email_snapshot=order.customer_email_snapshot,
        otp_hash=otp_digest,
        expires_at=now + timedelta(minutes=OTP_EXPIRY_MINUTES),
        attempt_count=0,
        resend_count=resend_count,
        delivery_failed=False,
        created_at=now,
    )
    session.add(verification_record)

    # Transactional outbox entry: stores zero plaintext OTP in the database
    outbox_record = GuestEmailOutbox(
        id=uuid4(),
        order_id=order.id,
        verification_id=verification_record.id,
        recipient_email=order.customer_email_snapshot,
        order_number=order.order_number,
        encrypted_otp_payload=encrypted_otp,
        status=OutboxDeliveryStatus.PENDING.value,
        attempts=0,
        max_attempts=MAX_OUTBOX_ATTEMPTS,
        created_at=now,
        updated_at=now,
    )
    session.add(outbox_record)
    session.commit()

    # Immediate outbox dispatch
    provider = email_provider or get_email_provider(settings)
    dispatch_outbox_job(
        session=session,
        outbox_id=outbox_record.id,
        settings=settings,
        email_provider=provider,
    )
    return True


def dispatch_outbox_job(
    session: Session,
    outbox_id: UUID,
    settings: Settings,
    email_provider: EmailDeliveryProvider | None = None,
) -> bool:
    """Attempt delivery of a single transactional email outbox job.

    Maintains delivery state, retry counts, and scrubs encrypted payload upon terminal state.
    """
    provider = email_provider or get_email_provider(settings)

    stmt = select(GuestEmailOutbox).where(GuestEmailOutbox.id == outbox_id)
    try:
        stmt = stmt.with_for_update()
    except Exception:
        pass

    job = session.scalar(stmt)
    if job is None or job.status in {
        OutboxDeliveryStatus.SENT.value,
        OutboxDeliveryStatus.FAILED.value,
    }:
        return False

    if not job.encrypted_otp_payload:
        return False

    now = utc_now()
    try:
        # Decrypt OTP in memory only for delivery
        otp_code = decrypt_otp(job.encrypted_otp_payload, settings.otp_hash_secret)
        provider.send_otp_email(job.recipient_email, job.order_number, otp_code)

        # Success: update status and scrub ciphertext
        job.status = OutboxDeliveryStatus.SENT.value
        job.sent_at = now
        job.encrypted_otp_payload = None
        job.updated_at = now
        session.commit()
        return True

    except Exception as exc:
        job.attempts += 1
        job.last_error = f"{type(exc).__name__}: {str(exc)}"
        job.updated_at = now

        # If max retries exhausted, transition to terminal failure
        if job.attempts >= job.max_attempts:
            job.status = OutboxDeliveryStatus.FAILED.value
            job.encrypted_otp_payload = None  # scrub ciphertext

            # Invalidate the verification record so an undelivered OTP is not left active,
            # and mark delivery_failed so customer is not locked out by cooldown/limits
            verification = session.scalar(
                select(GuestOrderVerification).where(
                    GuestOrderVerification.id == job.verification_id
                )
            )
            if verification:
                verification.consumed_at = now
                verification.delivery_failed = True

            session.commit()
            logger.error(
                "Outbox job permanently failed after max attempts",
                extra={
                    "event": "outbox_permanent_failure",
                    "job_id": str(job.id),
                    "order_id": str(job.order_id),
                    "error": job.last_error,
                },
            )
        else:
            job.status = OutboxDeliveryStatus.RETRY.value
            backoff_seconds = 10 * job.attempts
            job.next_retry_at = now + timedelta(seconds=backoff_seconds)
            session.commit()
            logger.warning(
                "Outbox job temporary failure, scheduled for retry",
                extra={
                    "event": "outbox_retry_scheduled",
                    "job_id": str(job.id),
                    "attempts": job.attempts,
                    "next_retry_at": job.next_retry_at.isoformat(),
                },
            )

        raise exc


def process_email_outbox(
    session: Session,
    settings: Settings,
    email_provider: EmailDeliveryProvider | None = None,
    limit: int = 10,
) -> int:
    """Process pending and retryable email outbox jobs."""
    now = utc_now()
    jobs = list(
        session.scalars(
            select(GuestEmailOutbox)
            .where(
                GuestEmailOutbox.status.in_(
                    [OutboxDeliveryStatus.PENDING.value, OutboxDeliveryStatus.RETRY.value]
                ),
                (GuestEmailOutbox.next_retry_at.is_(None))
                | (GuestEmailOutbox.next_retry_at <= now),
            )
            .order_by(GuestEmailOutbox.created_at.asc())
            .limit(limit)
        ).all()
    )

    success_count = 0
    provider = email_provider or get_email_provider(settings)
    for job in jobs:
        try:
            if dispatch_outbox_job(session, job.id, settings, provider):
                success_count += 1
        except Exception:
            continue
    return success_count


def verify_guest_otp(
    session: Session,
    order_number: str,
    email: str,
    otp: str,
    settings: Settings,
) -> tuple[Order, str, int]:
    """Validate guest OTP, create a tracked session, and issue a restricted JWT.

    Locks the Order row using SELECT FOR UPDATE in PostgreSQL to strictly serialize concurrent
    verification and resend operations.
    """
    if not settings.otp_hash_secret:
        raise MissingSecretError(
            "OTP verification service is currently misconfigured."
        )

    clean_order_number = order_number.strip().upper()
    clean_email = email.strip().lower()
    clean_otp = otp.strip()

    # Row-lock Order first to serialize concurrent requests and avoid deadlock with request_guest_verification
    stmt = select(Order).where(Order.order_number == clean_order_number)
    try:
        stmt = stmt.with_for_update()
    except Exception:
        pass

    order = session.scalar(stmt)

    if (
        order is None
        or not order.customer_email_snapshot
        or order.customer_email_snapshot.strip().lower() != clean_email
    ):
        raise InvalidOTPError("Invalid order number, email, or verification code.")

    now = utc_now()

    # Find the latest unconsumed verification record
    active_stmt = (
        select(GuestOrderVerification)
        .where(
            GuestOrderVerification.order_id == order.id,
            GuestOrderVerification.consumed_at.is_(None),
        )
        .order_by(GuestOrderVerification.created_at.desc())
        .limit(1)
    )
    try:
        active_stmt = active_stmt.with_for_update()
    except Exception:
        pass

    record = session.scalar(active_stmt)

    # Retrieve recent records for this order (to detect invalidated/consumed OTPs)
    recent_stmt = (
        select(GuestOrderVerification)
        .where(GuestOrderVerification.order_id == order.id)
        .order_by(GuestOrderVerification.created_at.desc())
        .limit(10)
    )
    recent_records = list(session.scalars(recent_stmt).all())

    if record is None:
        # No active unconsumed record. Check if candidate matches any previously consumed/expired record
        for old_rec in recent_records:
            if verify_otp_hash(clean_otp, old_rec.otp_hash, settings.otp_hash_secret):
                raise ExpiredOTPError(
                    "Verification code has expired or has already been used. Please request a new code."
                )
        raise InvalidOTPError("Invalid order number, email, or verification code.")

    # Check attempt threshold (max 5 attempts) on active record
    if record.attempt_count >= MAX_VERIFICATION_ATTEMPTS:
        raise OTPAttemptsExceededError(
            "Maximum verification attempts exceeded. Please request a new code."
        )

    # Check expiry on active record
    expires_at_utc = ensure_utc(record.expires_at)
    if expires_at_utc is not None and expires_at_utc <= now:
        raise ExpiredOTPError(
            "Verification code has expired or has already been used. Please request a new code."
        )

    matches = verify_otp_hash(clean_otp, record.otp_hash, settings.otp_hash_secret)
    if not matches:
        # L-1: Check if candidate OTP matches a previously invalidated/consumed record for this order
        for old_rec in recent_records:
            if old_rec.id != record.id and verify_otp_hash(
                clean_otp, old_rec.otp_hash, settings.otp_hash_secret
            ):
                # DO NOT increment active record's attempt_count!
                raise ExpiredOTPError(
                    "Verification code has expired or has already been used. Please request a new code."
                )

        # Truly invalid code: atomically count this failed attempt in DB
        session.execute(
            update(GuestOrderVerification)
            .where(GuestOrderVerification.id == record.id)
            .values(attempt_count=GuestOrderVerification.attempt_count + 1)
        )
        session.commit()
        session.refresh(record)

        if record.attempt_count >= MAX_VERIFICATION_ATTEMPTS:
            raise OTPAttemptsExceededError(
                "Maximum verification attempts exceeded. Please request a new code."
            )
        raise InvalidOTPError("Invalid verification code.")

    # Valid OTP: mark verified and consumed (single-use), count this successful attempt
    record.attempt_count += 1
    record.verified_at = now
    record.consumed_at = now

    # Issue tracked guest session
    jti = str(uuid4())
    jti_hash = compute_jti_hash(jti)
    lifetime = settings.guest_token_expire_minutes
    session_exp = now + timedelta(minutes=lifetime)

    guest_session = GuestAccessSession(
        id=uuid4(),
        order_id=order.id,
        token_jti_hash=jti_hash,
        expires_at=session_exp,
        created_at=now,
    )
    session.add(guest_session)
    session.commit()

    token, token_exp = create_guest_access_token(
        order_id=order.id,
        order_number=order.order_number,
        email=order.customer_email_snapshot,
        jti=jti,
        settings=settings,
        expires_in_minutes=lifetime,
    )

    expires_in_seconds = int((token_exp - now).total_seconds())
    return order, token, expires_in_seconds
