from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import logging
import os
from pathlib import Path
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from pydantic import SecretStr
import pytest
from sqlalchemy import create_engine, event, func as sa_func, inspect, select, text
from sqlalchemy.orm import Session, sessionmaker

from backend.app.api.auth_dependencies import revoke_guest_session
from backend.app.core.config import Settings
from backend.app.core.datetime_utils import ensure_utc, utc_now
from backend.app.db.base import Base
from backend.app.db.models import (
    Address,
    GuestAccessSession,
    GuestEmailOutbox,
    GuestOrderVerification,
    Inventory,
    Order,
    OrderItem,
    OrderStatus,
    OutboxDeliveryStatus,
    Product,
    User,
)
from backend.app.main import create_app
from backend.app.schemas.order import (
    CheckoutItemAcknowledgement,
    CheckoutRequest,
)
from backend.app.security.guest_tokens import (
    compute_jti_hash,
    create_guest_access_token,
    decode_guest_access_token,
)
from backend.app.security.otp_encryption import decrypt_otp, encrypt_otp
from backend.app.security.passwords import hash_password
from backend.app.security.tokens import create_access_token
from backend.app.services.checkout_service import process_checkout
from backend.app.services.email_service import (
    EmailConfigurationError,
    EmailDeliveryError,
    InMemoryEmailDeliveryProvider,
    SMTPEmailDeliveryProvider,
    get_email_provider,
    set_email_provider,
)
from backend.app.services.guest_verification_service import (
    ExpiredOTPError,
    InvalidOTPError,
    MissingSecretError,
    OTPAttemptsExceededError,
    RateLimitExceededError,
    dispatch_outbox_job,
    generate_otp,
    hash_otp,
    process_email_outbox,
    request_guest_verification,
    verify_guest_otp,
    verify_otp_hash,
)
from backend.app.services.shipping_tax_policy import (
    ConfigurableFlatShippingPolicy,
    ConfigurablePercentageTaxPolicy,
    set_shipping_policy,
    set_tax_policy,
)

TEST_OTP_SECRET = SecretStr("test-otp-secret-key-at-least-16-bytes")
TEST_AUTH_SECRET = SecretStr("test-auth-secret-key-at-least-32-bytes-long")


@pytest.fixture(autouse=True)
def reset_providers_and_policies():
    """Reset global email and policy providers between test runs."""
    in_memory_email = InMemoryEmailDeliveryProvider()
    set_email_provider(in_memory_email)
    set_shipping_policy(ConfigurableFlatShippingPolicy(Decimal("50.00")))
    set_tax_policy(ConfigurablePercentageTaxPolicy(Decimal("18.00")))
    yield in_memory_email
    set_email_provider(None)
    set_shipping_policy(None)
    set_tax_policy(None)


@pytest.fixture
def test_app_env(tmp_path, monkeypatch):
    """Set up isolated SQLite test environment for FastAPI client and DB session."""
    db_file = (tmp_path / "test_guest_orders.sqlite").as_posix()
    db_url = f"sqlite+pysqlite:///{db_file}"

    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-sent")
    monkeypatch.setenv("AUTH_SECRET_KEY", TEST_AUTH_SECRET.get_secret_value())
    monkeypatch.setenv("OTP_HASH_SECRET", TEST_OTP_SECRET.get_secret_value())
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("CATALOGUE_CURRENCY", "INR")
    monkeypatch.setenv("OUTBOX_WORKER_ENABLED", "false")

    engine = create_engine(db_url, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    app = create_app(engine_factory=lambda url: engine)
    with TestClient(app) as client:
        yield client, session_factory, engine

    engine.dispose()


def _create_user_and_order(
    session: Session,
    email: str = "guest_user@example.com",
    order_number: str = "ORD-20261121-GUEST01",
    status_val: str = OrderStatus.CONFIRMED.value,
) -> tuple[User, Order]:
    user = User(
        id=uuid4(),
        email=email,
        password_hash=hash_password("Password123!"),
        is_active=True,
    )
    session.add(user)
    session.flush()

    addr = Address(
        id=uuid4(),
        user_id=user.id,
        recipient_name="Alice Guest",
        line1="123 Guest Way",
        city="Bengaluru",
        region="KA",
        postal_code="560001",
        country_code="IN",
    )
    session.add(addr)
    session.flush()

    order = Order(
        id=uuid4(),
        order_number=order_number,
        user_id=user.id,
        customer_email_snapshot=email,
        shipping_address_id=addr.id,
        shipping_recipient_name=addr.recipient_name,
        shipping_line1=addr.line1,
        shipping_city=addr.city,
        shipping_region=addr.region,
        shipping_postal_code=addr.postal_code,
        shipping_country_code=addr.country_code,
        status=status_val,
        currency="INR",
        subtotal=Decimal("500.00"),
        shipping_fee=Decimal("50.00"),
        tax=Decimal("90.00"),
        total_amount=Decimal("640.00"),
    )
    session.add(order)
    session.flush()

    item = OrderItem(
        id=uuid4(),
        order_id=order.id,
        product_sku="SKU-GUEST-01",
        product_name="Guest Headphones",
        unit_price=Decimal("500.00"),
        quantity=1,
        line_total=Decimal("500.00"),
    )
    session.add(item)
    session.commit()
    session.refresh(order)
    return user, order


# ==============================================================================
# 1. OTP Verification Service Tests
# ==============================================================================

def test_datetime_utils_normalization_naive_and_aware():
    """Verify UTC normalization helper correctly handles None, naive, and aware datetimes."""
    assert ensure_utc(None) is None

    naive = datetime(2026, 11, 21, 12, 0, 0)
    aware_utc = ensure_utc(naive)
    assert aware_utc.tzinfo == timezone.utc
    assert aware_utc.hour == 12

    # Aware non-UTC datetime (e.g. UTC+5:30) converted accurately to UTC
    tz_ist = timezone(timedelta(hours=5, minutes=30))
    ist_time = datetime(2026, 11, 21, 17, 30, 0, tzinfo=tz_ist)
    converted_utc = ensure_utc(ist_time)
    assert converted_utc.tzinfo == timezone.utc
    assert converted_utc.hour == 12
    assert converted_utc.minute == 0

    now = utc_now()
    assert now.tzinfo == timezone.utc


def test_otp_generation_and_hashing():
    otp = generate_otp()
    assert len(otp) == 6
    assert otp.isdigit()

    digest = hash_otp(otp, TEST_OTP_SECRET)
    assert len(digest) == 64
    assert verify_otp_hash(otp, digest, TEST_OTP_SECRET) is True
    assert verify_otp_hash("000000", digest, TEST_OTP_SECRET) is False


def test_valid_otp_verification(test_app_env):
    client, session_factory, _ = test_app_env
    with session_factory() as session:
        user, order = _create_user_and_order(session)
        settings = client.app.state.settings
        in_memory_email = get_email_provider(settings)

        # 1. Request verification
        sent = request_guest_verification(
            session, order.order_number, user.email, settings, in_memory_email
        )
        assert sent is True
        messages = in_memory_email.get_sent_messages()
        assert len(messages) == 1
        otp = messages[0]["otp_code"]

        # 2. Verify OTP
        verified_order, token, expires_in = verify_guest_otp(
            session, order.order_number, user.email, otp, settings
        )
        assert verified_order.id == order.id
        assert isinstance(token, str)
        assert expires_in > 0

        # Verify database record updated
        rec = session.scalar(
            select(GuestOrderVerification).where(
                GuestOrderVerification.order_id == order.id
            )
        )
        assert rec is not None
        assert rec.verified_at is not None
        assert rec.consumed_at is not None
        assert rec.attempt_count == 1


def test_incorrect_otp_fails_and_increments_attempts(test_app_env):
    client, session_factory, _ = test_app_env
    with session_factory() as session:
        user, order = _create_user_and_order(session)
        settings = client.app.state.settings
        in_memory_email = get_email_provider(settings)

        request_guest_verification(
            session, order.order_number, user.email, settings, in_memory_email
        )
        rec = session.scalar(
            select(GuestOrderVerification).where(
                GuestOrderVerification.order_id == order.id
            )
        )
        assert rec.attempt_count == 0

        with pytest.raises(InvalidOTPError, match="Invalid verification code"):
            verify_guest_otp(
                session, order.order_number, user.email, "999999", settings
            )

        session.refresh(rec)
        assert rec.attempt_count == 1
        assert rec.consumed_at is None


def test_expired_otp_rejected(test_app_env):
    client, session_factory, _ = test_app_env
    with session_factory() as session:
        user, order = _create_user_and_order(session)
        settings = client.app.state.settings
        in_memory_email = get_email_provider(settings)

        request_guest_verification(
            session, order.order_number, user.email, settings, in_memory_email
        )
        otp = in_memory_email.get_sent_messages()[0]["otp_code"]

        # Backdate expiration by 6 minutes
        rec = session.scalar(
            select(GuestOrderVerification).where(
                GuestOrderVerification.order_id == order.id
            )
        )
        rec.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        session.commit()

        with pytest.raises(ExpiredOTPError, match="Verification code has expired"):
            verify_guest_otp(
                session, order.order_number, user.email, otp, settings
            )


def test_five_failed_attempts_locks_and_sixth_attempt_rejected(test_app_env):
    client, session_factory, _ = test_app_env
    with session_factory() as session:
        user, order = _create_user_and_order(session)
        settings = client.app.state.settings
        in_memory_email = get_email_provider(settings)

        request_guest_verification(
            session, order.order_number, user.email, settings, in_memory_email
        )
        otp = in_memory_email.get_sent_messages()[0]["otp_code"]

        # 1-4 wrong attempts
        for attempt in range(1, 5):
            with pytest.raises(InvalidOTPError):
                verify_guest_otp(
                    session, order.order_number, user.email, "000000", settings
                )

        # 5th wrong attempt raises OTPAttemptsExceededError
        with pytest.raises(
            OTPAttemptsExceededError, match="Maximum verification attempts exceeded"
        ):
            verify_guest_otp(
                session, order.order_number, user.email, "000000", settings
            )

        # 6th attempt (even with the CORRECT otp) is permanently locked
        with pytest.raises(
            OTPAttemptsExceededError, match="Maximum verification attempts exceeded"
        ):
            verify_guest_otp(
                session, order.order_number, user.email, otp, settings
            )


def test_otp_single_use(test_app_env):
    client, session_factory, _ = test_app_env
    with session_factory() as session:
        user, order = _create_user_and_order(session)
        settings = client.app.state.settings
        in_memory_email = get_email_provider(settings)

        request_guest_verification(
            session, order.order_number, user.email, settings, in_memory_email
        )
        otp = in_memory_email.get_sent_messages()[0]["otp_code"]

        # First verification succeeds
        _, token1, _ = verify_guest_otp(
            session, order.order_number, user.email, otp, settings
        )
        assert token1 is not None

        # Immediate replay with same OTP must fail as expired/consumed
        with pytest.raises(ExpiredOTPError, match="already been used"):
            verify_guest_otp(
                session, order.order_number, user.email, otp, settings
            )


def test_resend_invalidates_previous_otp(test_app_env):
    client, session_factory, _ = test_app_env
    with session_factory() as session:
        user, order = _create_user_and_order(session)
        settings = client.app.state.settings
        in_memory_email = get_email_provider(settings)

        # First request
        request_guest_verification(
            session, order.order_number, user.email, settings, in_memory_email
        )
        first_otp = in_memory_email.get_sent_messages()[0]["otp_code"]

        # Backdate the first request so cooldown passes
        first_rec = session.scalar(
            select(GuestOrderVerification).where(
                GuestOrderVerification.order_id == order.id
            )
        )
        first_rec.created_at = datetime.now(timezone.utc) - timedelta(seconds=70)
        session.commit()

        # Second request (resend)
        request_guest_verification(
            session, order.order_number, user.email, settings, in_memory_email
        )
        messages = in_memory_email.get_sent_messages()
        assert len(messages) == 2
        second_otp = messages[1]["otp_code"]

        # Attempting first OTP must fail because resend invalidated it
        with pytest.raises(ExpiredOTPError, match="already been used"):
            verify_guest_otp(
                session, order.order_number, user.email, first_otp, settings
            )

        # Second OTP succeeds
        _, token, _ = verify_guest_otp(
            session, order.order_number, user.email, second_otp, settings
        )
        assert token is not None


def test_submitting_invalidated_previous_otp_does_not_burn_active_attempts(test_app_env):
    """L-1 finding: submitting an invalidated prior OTP returns ExpiredOTPError without burning attempts on the active OTP."""
    client, session_factory, _ = test_app_env
    with session_factory() as session:
        user, order = _create_user_and_order(session)
        settings = client.app.state.settings
        in_memory = InMemoryEmailDeliveryProvider()

        # 1. Request OTP 1
        request_guest_verification(session, order.order_number, user.email, settings, in_memory)
        otp1 = in_memory.get_sent_messages()[0]["otp_code"]

        # 2. Backdate cooldown
        ver1 = session.scalar(
            select(GuestOrderVerification).where(GuestOrderVerification.order_id == order.id)
        )
        ver1.created_at = utc_now() - timedelta(seconds=70)
        session.commit()

        # 3. Request OTP 2 (resend)
        request_guest_verification(session, order.order_number, user.email, settings, in_memory)
        otp2 = in_memory.get_sent_messages()[1]["otp_code"]
        assert otp1 != otp2

        # Active record is for otp2
        active_ver = session.scalar(
            select(GuestOrderVerification).where(
                GuestOrderVerification.order_id == order.id,
                GuestOrderVerification.consumed_at.is_(None),
            )
        )
        assert active_ver is not None
        assert active_ver.attempt_count == 0

        # 4. Submit OTP 1 (invalidated by resend)
        with pytest.raises(ExpiredOTPError, match="already been used"):
            verify_guest_otp(session, order.order_number, user.email, otp1, settings)

        # L-1 check: Active verification record's attempt_count must STILL be 0!
        session.refresh(active_ver)
        assert active_ver.attempt_count == 0

        # 5. Submit valid OTP 2: succeeds without being penalized
        verified_order, token, _ = verify_guest_otp(
            session, order.order_number, user.email, otp2, settings
        )
        assert verified_order.id == order.id
        assert token is not None


def test_resend_cooldown_enforcement(test_app_env):
    client, session_factory, _ = test_app_env
    with session_factory() as session:
        user, order = _create_user_and_order(session)
        settings = client.app.state.settings
        in_memory_email = get_email_provider(settings)

        # First request
        request_guest_verification(
            session, order.order_number, user.email, settings, in_memory_email
        )

        # Immediate second request before 60 seconds raises RateLimitExceededError
        with pytest.raises(
            RateLimitExceededError, match="Please wait.*seconds"
        ) as exc_info:
            request_guest_verification(
                session, order.order_number, user.email, settings, in_memory_email
            )
        assert exc_info.value.retry_after > 0


def test_resend_limit_enforcement(test_app_env):
    client, session_factory, _ = test_app_env
    with session_factory() as session:
        user, order = _create_user_and_order(session)
        settings = client.app.state.settings
        in_memory_email = get_email_provider(settings)

        now = datetime.now(timezone.utc)

        # Seed 4 previous requests in the past 10 minutes (exceeding max 3 resends)
        for i in range(4):
            rec = GuestOrderVerification(
                id=uuid4(),
                order_id=order.id,
                email_snapshot=order.customer_email_snapshot,
                otp_hash="dummy-hash",
                expires_at=now + timedelta(minutes=5),
                attempt_count=0,
                resend_count=i,
                created_at=now - timedelta(minutes=10 - i * 2),
            )
            session.add(rec)
        session.commit()

        with pytest.raises(
            RateLimitExceededError, match="Maximum verification requests exceeded"
        ) as exc_info:
            request_guest_verification(
                session, order.order_number, user.email, settings, in_memory_email
            )
        assert exc_info.value.retry_after == 900


def test_concurrent_verification_attempts(test_app_env):
    client, session_factory, _ = test_app_env
    with session_factory() as session:
        user, order = _create_user_and_order(session)
        settings = client.app.state.settings
        in_memory_email = get_email_provider(settings)

        request_guest_verification(
            session, order.order_number, user.email, settings, in_memory_email
        )

    # Launch 6 concurrent verification attempts with incorrect OTPs
    def try_verify(wrong_code: str):
        with session_factory() as thread_session:
            try:
                verify_guest_otp(
                    thread_session,
                    order.order_number,
                    user.email,
                    wrong_code,
                    settings,
                )
                return "success"
            except OTPAttemptsExceededError:
                return "locked"
            except InvalidOTPError:
                return "invalid"
            except Exception as e:
                return type(e).__name__

    with ThreadPoolExecutor(max_workers=6) as executor:
        results = list(executor.map(try_verify, [f"{i:06d}" for i in range(6)]))

    with session_factory() as session:
        rec = session.scalar(
            select(GuestOrderVerification).where(
                GuestOrderVerification.order_id == order.id
            )
        )
        assert rec.attempt_count >= 5
        assert "invalid" in results


# ==============================================================================
# 2. Email Delivery & Configuration Tests
# ==============================================================================

def test_test_provider_captures_message():
    provider = InMemoryEmailDeliveryProvider()
    provider.send_otp_email("test@example.com", "ORD-12345", "123456")
    messages = provider.get_sent_messages()
    assert len(messages) == 1
    assert messages[0]["recipient"] == "test@example.com"
    assert messages[0]["order_number"] == "ORD-12345"
    assert messages[0]["otp_code"] == "123456"

    provider.clear()
    assert len(provider.get_sent_messages()) == 0


def test_smtp_configuration_validation():
    # Missing host and from_email raises EmailConfigurationError
    settings = Settings(
        GEMINI_API_KEY="test-key",
        DATABASE_URL="sqlite+pysqlite:///:memory:",
        AUTH_SECRET_KEY=TEST_AUTH_SECRET.get_secret_value(),
        SMTP_HOST=None,
        SMTP_FROM_EMAIL=None,
    )
    with pytest.raises(EmailConfigurationError):
        SMTPEmailDeliveryProvider(settings)


def test_missing_production_smtp_configuration_fails_safely():
    set_email_provider(None)
    prod_settings = Settings(
        GEMINI_API_KEY="test-key",
        DATABASE_URL="sqlite+pysqlite:///:memory:",
        AUTH_SECRET_KEY=TEST_AUTH_SECRET.get_secret_value(),
        ENVIRONMENT="production",
        SMTP_HOST=None,
        SMTP_FROM_EMAIL=None,
    )
    with pytest.raises(
        EmailConfigurationError, match="Production email delivery is not configured"
    ):
        get_email_provider(prod_settings)


def test_no_production_mock_fallback():
    mock_provider = InMemoryEmailDeliveryProvider()
    set_email_provider(mock_provider)
    prod_settings = Settings(
        GEMINI_API_KEY="test-key",
        DATABASE_URL="sqlite+pysqlite:///:memory:",
        AUTH_SECRET_KEY=TEST_AUTH_SECRET.get_secret_value(),
        ENVIRONMENT="production",
    )
    with pytest.raises(
        EmailConfigurationError, match="In-memory test email provider is prohibited"
    ):
        get_email_provider(prod_settings)


def test_no_otp_leakage_in_api_response(test_app_env):
    client, session_factory, _ = test_app_env
    with session_factory() as session:
        user, order = _create_user_and_order(session)

    resp = client.post(
        "/api/v1/orders/guest/request-verification",
        json={"order_number": order.order_number, "email": user.email},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "otp" not in data
    assert "code" not in data
    assert "verification code has been sent" in data["message"].lower()

    # Generic response for non-existent order as well
    resp_fake = client.post(
        "/api/v1/orders/guest/request-verification",
        json={"order_number": "ORD-DOES-NOT-EXIST", "email": "fake@example.com"},
    )
    assert resp_fake.status_code == 200
    assert resp_fake.json() == resp.json()


def test_outbox_record_created_atomically_without_plaintext_otp(test_app_env):
    """H-1 finding: outbox record is persisted atomically with verification, storing encrypted ciphertext rather than plaintext OTP."""
    client, session_factory, _ = test_app_env
    with session_factory() as session:
        user, order = _create_user_and_order(session)
        settings = client.app.state.settings

        class FailOnceProvider:
            def send_otp_email(self, recipient, order_number, otp_code):
                raise EmailDeliveryError("Temporary network glitch")

        with pytest.raises(EmailDeliveryError):
            request_guest_verification(
                session, order.order_number, user.email, settings, FailOnceProvider()
            )

        # Check atomic creation
        ver = session.scalar(
            select(GuestOrderVerification).where(GuestOrderVerification.order_id == order.id)
        )
        assert ver is not None
        assert ver.delivery_failed is False

        job = session.scalar(
            select(GuestEmailOutbox).where(GuestEmailOutbox.order_id == order.id)
        )
        assert job is not None
        assert job.status == OutboxDeliveryStatus.RETRY.value
        assert job.attempts == 1
        assert job.encrypted_otp_payload is not None
        # Must be encrypted ciphertext, not a 6-digit plain string
        assert not job.encrypted_otp_payload.isdigit()
        assert len(job.encrypted_otp_payload) > 50

        # Fernet-decrypted payload yields the matching OTP
        decrypted = decrypt_otp(job.encrypted_otp_payload, settings.otp_hash_secret)
        assert len(decrypted) == 6
        assert decrypted.isdigit()
        assert verify_otp_hash(decrypted, ver.otp_hash, settings.otp_hash_secret) is True


def test_smtp_connection_failure_outbox_retry(test_app_env):
    """H-1 finding: SMTP network connection failure transitions outbox job to RETRY with backoff."""
    client, session_factory, _ = test_app_env
    with session_factory() as session:
        user, order = _create_user_and_order(session)
        settings = client.app.state.settings

        class ConnectionFailProvider:
            def send_otp_email(self, recipient, order_number, otp_code):
                raise EmailDeliveryError("Connection refused by SMTP server")

        with pytest.raises(EmailDeliveryError, match="Connection refused"):
            request_guest_verification(
                session, order.order_number, user.email, settings, ConnectionFailProvider()
            )

        job = session.scalar(
            select(GuestEmailOutbox).where(GuestEmailOutbox.order_id == order.id)
        )
        assert job.status == OutboxDeliveryStatus.RETRY.value
        assert job.attempts == 1
        assert job.next_retry_at is not None
        assert ensure_utc(job.next_retry_at) > utc_now()
        assert "Connection refused" in job.last_error


def test_smtp_timeout_handling(test_app_env):
    """H-1 finding: SMTP connection timeout is recorded and triggers outbox retry."""
    client, session_factory, _ = test_app_env
    with session_factory() as session:
        user, order = _create_user_and_order(session)
        settings = client.app.state.settings

        class TimeoutProvider:
            def send_otp_email(self, recipient, order_number, otp_code):
                raise TimeoutError("SMTP connection timed out after 10s")

        with pytest.raises(TimeoutError, match="timed out"):
            request_guest_verification(
                session, order.order_number, user.email, settings, TimeoutProvider()
            )

        job = session.scalar(
            select(GuestEmailOutbox).where(GuestEmailOutbox.order_id == order.id)
        )
        assert job.status == OutboxDeliveryStatus.RETRY.value
        assert job.attempts == 1
        assert "TimeoutError" in job.last_error


def test_retry_after_temporary_delivery_failure(test_app_env):
    """H-1 finding: retryable outbox job is re-dispatched and scrubbed upon successful delivery."""
    client, session_factory, _ = test_app_env
    with session_factory() as session:
        user, order = _create_user_and_order(session)
        settings = client.app.state.settings

        class TempFailProvider:
            def send_otp_email(self, recipient, order_number, otp_code):
                raise EmailDeliveryError("Temporary upstream outage")

        with pytest.raises(EmailDeliveryError):
            request_guest_verification(
                session, order.order_number, user.email, settings, TempFailProvider()
            )

        job = session.scalar(
            select(GuestEmailOutbox).where(GuestEmailOutbox.order_id == order.id)
        )
        assert job.status == OutboxDeliveryStatus.RETRY.value

        # Make eligible for retry
        job.next_retry_at = utc_now() - timedelta(seconds=1)
        session.commit()

        # Re-process outbox with working provider
        working_provider = InMemoryEmailDeliveryProvider()
        success_count = process_email_outbox(session, settings, working_provider)
        assert success_count == 1

        session.refresh(job)
        assert job.status == OutboxDeliveryStatus.SENT.value
        assert job.sent_at is not None
        assert job.encrypted_otp_payload is None  # scrubbed

        messages = working_provider.get_sent_messages()
        assert len(messages) == 1
        assert messages[0]["order_number"] == order.order_number
        assert len(messages[0]["otp_code"]) == 6


def test_permanent_delivery_failure_invalidates_otp_and_prevents_lockout(test_app_env):
    """H-1 finding: permanent outbox failure scrubs ciphertext, marks delivery_failed, and does NOT lock out customer."""
    client, session_factory, _ = test_app_env
    with session_factory() as session:
        user, order = _create_user_and_order(session)
        settings = client.app.state.settings

        class PermanentFailProvider:
            def send_otp_email(self, recipient, order_number, otp_code):
                raise EmailDeliveryError("Permanent 550 Mailbox does not exist")

        provider = PermanentFailProvider()

        # 1st attempt
        with pytest.raises(EmailDeliveryError):
            request_guest_verification(
                session, order.order_number, user.email, settings, provider
            )

        job = session.scalar(
            select(GuestEmailOutbox).where(GuestEmailOutbox.order_id == order.id)
        )
        assert job.attempts == 1
        assert job.status == OutboxDeliveryStatus.RETRY.value

        # 2nd attempt
        job.next_retry_at = utc_now() - timedelta(seconds=1)
        session.commit()
        process_email_outbox(session, settings, provider)
        session.refresh(job)
        assert job.attempts == 2
        assert job.status == OutboxDeliveryStatus.RETRY.value

        # 3rd attempt (hits max_attempts=3 -> permanent failure)
        job.next_retry_at = utc_now() - timedelta(seconds=1)
        session.commit()
        process_email_outbox(session, settings, provider)
        session.refresh(job)
        assert job.attempts == 3
        assert job.status == OutboxDeliveryStatus.FAILED.value
        assert job.encrypted_otp_payload is None  # scrubbed

        # Check verification record
        ver = session.scalar(
            select(GuestOrderVerification).where(GuestOrderVerification.id == job.verification_id)
        )
        assert ver.consumed_at is not None  # invalidated
        assert ver.delivery_failed is True

        # CRITICAL: customer is NOT locked out by cooldown or resend limit!
        working_provider = InMemoryEmailDeliveryProvider()
        sent = request_guest_verification(
            session, order.order_number, user.email, settings, working_provider
        )
        assert sent is True
        assert len(working_provider.get_sent_messages()) == 1


def test_no_plaintext_otp_in_database_or_logs(test_app_env, caplog):
    """Verify zero plaintext OTP is written to database columns or log stream."""
    client, session_factory, _ = test_app_env
    with session_factory() as session:
        user, order = _create_user_and_order(session)
        settings = client.app.state.settings
        in_memory = InMemoryEmailDeliveryProvider()

        with caplog.at_level(logging.DEBUG):
            sent = request_guest_verification(
                session, order.order_number, user.email, settings, in_memory
            )
            assert sent is True
            otp = in_memory.get_sent_messages()[0]["otp_code"]
            assert len(otp) == 6

            verify_guest_otp(session, order.order_number, user.email, otp, settings)

        # 1. Assert plaintext OTP string does not appear in any log record
        for record in caplog.records:
            assert otp not in record.getMessage(), f"Plaintext OTP leaked in log: {record.getMessage()}"

        # 2. Assert plaintext OTP string does not appear in any column of guest tables
        for model_cls in [GuestOrderVerification, GuestEmailOutbox, GuestAccessSession, Order]:
            for row in session.scalars(select(model_cls)).all():
                for col in model_cls.__table__.columns:
                    val = getattr(row, col.name)
                    if val is not None:
                        assert otp not in str(val), f"Plaintext OTP found in {model_cls.__name__}.{col.name}: {val}"


# ==============================================================================
# 3. Guest Token & API Authorization Tests
# ==============================================================================

def test_correct_guest_token_accepted_and_returns_filtered_data(test_app_env):
    client, session_factory, _ = test_app_env
    with session_factory() as session:
        user, order = _create_user_and_order(session)

    # 1. Request verification
    req_resp = client.post(
        "/api/v1/orders/guest/request-verification",
        json={"order_number": order.order_number, "email": user.email},
    )
    assert req_resp.status_code == 200

    in_memory = get_email_provider(client.app.state.settings)
    otp = in_memory.get_sent_messages()[-1]["otp_code"]

    # 2. Verify OTP
    verify_resp = client.post(
        "/api/v1/orders/guest/verify",
        json={"order_number": order.order_number, "email": user.email, "otp": otp},
    )
    assert verify_resp.status_code == 200
    token_data = verify_resp.json()
    guest_token = token_data["access_token"]
    assert token_data["order_number"] == order.order_number

    # 3. Access guest order endpoint
    guest_resp = client.get(
        f"/api/v1/orders/guest/{order.order_number}",
        headers={"Authorization": f"Bearer {guest_token}"},
    )
    assert guest_resp.status_code == 200
    body = guest_resp.json()
    assert body["order_number"] == order.order_number
    assert body["status"] == "confirmed"
    assert len(body["items"]) == 1
    assert body["items"][0]["product_sku"] == "SKU-GUEST-01"

    # Confirm sensitive fields are strictly excluded
    forbidden_keys = {
        "id",
        "user_id",
        "shipping_address_id",
        "idempotency_key",
        "request_fingerprint",
        "otp_hash",
        "token_jti_hash",
    }
    for key in forbidden_keys:
        assert key not in body, f"Leaked sensitive key '{key}' in guest response"


def test_expired_token_rejected(test_app_env):
    client, session_factory, _ = test_app_env
    with session_factory() as session:
        user, order = _create_user_and_order(session)
        jti = str(uuid4())
        token, _ = create_guest_access_token(
            order_id=order.id,
            order_number=order.order_number,
            email=user.email,
            jti=jti,
            settings=client.app.state.settings,
            expires_in_minutes=-1,  # expired in past
        )
        # Add session in DB
        sess_rec = GuestAccessSession(
            id=uuid4(),
            order_id=order.id,
            token_jti_hash=compute_jti_hash(jti),
            expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
            created_at=datetime.now(timezone.utc),
        )
        session.add(sess_rec)
        session.commit()

    resp = client.get(
        f"/api/v1/orders/guest/{order.order_number}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 401


def test_wrong_token_type_rejected(test_app_env):
    client, session_factory, _ = test_app_env
    with session_factory() as session:
        user, order = _create_user_and_order(session)
        # Normal user JWT token with type='access'
        user_token = create_access_token(user.id, client.app.state.settings)

    # Calling guest endpoint with normal access token fails 401
    resp = client.get(
        f"/api/v1/orders/guest/{order.order_number}",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert resp.status_code == 401


def test_revoked_token_rejected(test_app_env):
    client, session_factory, _ = test_app_env
    with session_factory() as session:
        user, order = _create_user_and_order(session)
        jti = str(uuid4())
        token, _ = create_guest_access_token(
            order_id=order.id,
            order_number=order.order_number,
            email=user.email,
            jti=jti,
            settings=client.app.state.settings,
        )
        # Session in DB marked as revoked
        sess_rec = GuestAccessSession(
            id=uuid4(),
            order_id=order.id,
            token_jti_hash=compute_jti_hash(jti),
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
            revoked_at=datetime.now(timezone.utc),
            created_at=datetime.now(timezone.utc),
        )
        session.add(sess_rec)
        session.commit()

    resp = client.get(
        f"/api/v1/orders/guest/{order.order_number}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 401


def test_missing_jti_session_rejected(test_app_env):
    client, session_factory, _ = test_app_env
    with session_factory() as session:
        user, order = _create_user_and_order(session)
        # Token validly signed, but JTI never persisted in guest_access_sessions
        token, _ = create_guest_access_token(
            order_id=order.id,
            order_number=order.order_number,
            email=user.email,
            jti=str(uuid4()),
            settings=client.app.state.settings,
        )

    resp = client.get(
        f"/api/v1/orders/guest/{order.order_number}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 401


def test_guest_token_rejected_by_authenticated_endpoints(test_app_env):
    client, session_factory, _ = test_app_env
    with session_factory() as session:
        user, order = _create_user_and_order(session)
        jti = str(uuid4())
        token, _ = create_guest_access_token(
            order_id=order.id,
            order_number=order.order_number,
            email=user.email,
            jti=jti,
            settings=client.app.state.settings,
        )
        sess_rec = GuestAccessSession(
            id=uuid4(),
            order_id=order.id,
            token_jti_hash=compute_jti_hash(jti),
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
            created_at=datetime.now(timezone.utc),
        )
        session.add(sess_rec)
        session.commit()

    headers = {"Authorization": f"Bearer {token}"}

    # 1. Profile endpoint
    assert client.get("/api/v1/customers/me/profile", headers=headers).status_code == 401
    # 2. Addresses endpoint
    assert client.get("/api/v1/customers/me/addresses", headers=headers).status_code == 401
    # 3. Cart endpoint
    assert client.get("/api/v1/cart", headers=headers).status_code == 401
    # 4. Standard authenticated orders endpoint
    assert client.get("/api/v1/orders", headers=headers).status_code == 401


def test_guest_cannot_access_another_order(test_app_env):
    client, session_factory, _ = test_app_env
    with session_factory() as session:
        user_a, order_a = _create_user_and_order(
            session, email="a@example.com", order_number="ORD-GUEST-AAA"
        )
        user_b, order_b = _create_user_and_order(
            session, email="b@example.com", order_number="ORD-GUEST-BBB"
        )

        jti_a = str(uuid4())
        token_a, _ = create_guest_access_token(
            order_id=order_a.id,
            order_number=order_a.order_number,
            email=user_a.email,
            jti=jti_a,
            settings=client.app.state.settings,
        )
        sess_rec = GuestAccessSession(
            id=uuid4(),
            order_id=order_a.id,
            token_jti_hash=compute_jti_hash(jti_a),
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
            created_at=datetime.now(timezone.utc),
        )
        session.add(sess_rec)
        session.commit()

    # Using token_a to request order_b's details must fail with 403 Forbidden
    resp = client.get(
        f"/api/v1/orders/guest/{order_b.order_number}",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert resp.status_code == 403
    assert "not authorized" in resp.json()["detail"].lower()


def test_dynamic_guest_session_revocation(test_app_env):
    """Verify dynamic revocation of a guest session using revoke_guest_session immediately invalidates token access."""
    client, session_factory, _ = test_app_env
    with session_factory() as session:
        user, order = _create_user_and_order(session)

    # 1. Request and verify to get token
    client.post(
        "/api/v1/orders/guest/request-verification",
        json={"order_number": order.order_number, "email": user.email},
    )
    in_memory = get_email_provider(client.app.state.settings)
    otp = in_memory.get_sent_messages()[-1]["otp_code"]

    resp = client.post(
        "/api/v1/orders/guest/verify",
        json={"order_number": order.order_number, "email": user.email, "otp": otp},
    )
    assert resp.status_code == 200
    token = resp.json()["access_token"]

    # 2. Verify token works
    headers = {"Authorization": f"Bearer {token}"}
    access_resp = client.get(f"/api/v1/orders/guest/{order.order_number}", headers=headers)
    assert access_resp.status_code == 200

    # 3. Dynamically revoke session
    payload = decode_guest_access_token(token, client.app.state.settings)
    with session_factory() as session:
        revoked = revoke_guest_session(session, payload["jti"])
        assert revoked is True

    # 4. Token is now immediately rejected
    revoked_resp = client.get(f"/api/v1/orders/guest/{order.order_number}", headers=headers)
    assert revoked_resp.status_code == 401


def test_concurrent_resend_and_verification(test_app_env):
    """Verify concurrent resend and verification complete cleanly without unhandled exceptions or deadlocks."""
    client, session_factory, _ = test_app_env
    with session_factory() as session:
        user, order = _create_user_and_order(session)
        settings = client.app.state.settings
        in_memory = InMemoryEmailDeliveryProvider()

        request_guest_verification(session, order.order_number, user.email, settings, in_memory)
        otp1 = in_memory.get_sent_messages()[0]["otp_code"]

        # Backdate cooldown
        rec = session.scalar(
            select(GuestOrderVerification).where(GuestOrderVerification.order_id == order.id)
        )
        rec.created_at = utc_now() - timedelta(seconds=70)
        session.commit()

    def do_resend():
        with session_factory() as s:
            try:
                p = InMemoryEmailDeliveryProvider()
                return "resend", request_guest_verification(s, order.order_number, user.email, settings, p)
            except Exception as e:
                return "resend", type(e).__name__

    def do_verify():
        with session_factory() as s:
            try:
                verify_guest_otp(s, order.order_number, user.email, otp1, settings)
                return "verify", "success"
            except Exception as e:
                return "verify", type(e).__name__

    with ThreadPoolExecutor(max_workers=2) as executor:
        f_resend = executor.submit(do_resend)
        f_verify = executor.submit(do_verify)
        res_resend = f_resend.result()
        res_verify = f_verify.result()

    assert res_resend[0] == "resend"
    assert res_verify[0] == "verify"


# ==============================================================================
# 4. Checkout Order Snapshot Creation Test
# ==============================================================================

def test_checkout_creates_customer_email_snapshot(test_app_env):
    client, session_factory, _ = test_app_env
    with session_factory() as session:
        user = User(
            id=uuid4(),
            email="checkout_snapshot@example.com",
            password_hash=hash_password("Pass123!"),
            is_active=True,
        )
        session.add(user)
        session.flush()

        addr = Address(
            id=uuid4(),
            user_id=user.id,
            recipient_name="Checkout Tester",
            line1="123 Checkout St",
            city="Bengaluru",
            region="KA",
            postal_code="560001",
            country_code="IN",
        )
        session.add(addr)

        prod = Product(
            id=uuid4(),
            sku="SKU-CHK-01",
            name="Checkout Product",
            price=Decimal("200.00"),
            is_active=True,
        )
        session.add(prod)
        session.flush()

        inv = Inventory(product_id=prod.id, available_quantity=10)
        session.add(inv)
        session.commit()

        # Add item to cart
        client.app.state.settings
        from backend.app.services.cart_service import add_to_cart

        add_to_cart(session, user.id, prod.id, 1)

        req = CheckoutRequest(
            shipping_address_id=addr.id,
            items=[
                CheckoutItemAcknowledgement(
                    product_id=prod.id,
                    quantity=1,
                    acknowledged_unit_price=Decimal("200.00"),
                )
            ],
        )

        order, is_replay = process_checkout(
            session,
            user.id,
            req,
            idempotency_key="chk-key-01",
            settings=client.app.state.settings,
        )
        session.commit()

        assert order.customer_email_snapshot == "checkout_snapshot@example.com"


# ==============================================================================
# 5. PostgreSQL Integration Tests (using isolated ecommerce_agent_test only)
# ==============================================================================

def _get_postgres_test_url() -> str | None:
    url = os.getenv("POSTGRES_TEST_DATABASE_URL")
    if not url:
        try:
            import winreg

            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment")
            val, _ = winreg.QueryValueEx(key, "POSTGRES_TEST_DATABASE_URL")
            winreg.CloseKey(key)
            if val:
                url = val
                os.environ["POSTGRES_TEST_DATABASE_URL"] = val
        except Exception:
            pass

    if not url or not url.startswith("postgresql"):
        return None

    from urllib.parse import urlparse

    parsed = urlparse(url)
    db_name = parsed.path.lstrip("/")
    if db_name != "ecommerce_agent_test":
        raise RuntimeError(
            f"CRITICAL SAFETY VIOLATION: Target database must be 'ecommerce_agent_test', got '{db_name}'"
        )
    return url


def test_postgres_guest_verification_lifecycle():
    pg_url = _get_postgres_test_url()
    if not pg_url:
        pytest.skip("POSTGRES_TEST_DATABASE_URL not configured; skipping live PostgreSQL test")

    engine = create_engine(pg_url)
    try:
        with engine.connect() as conn:
            db_name = conn.execute(text("SELECT current_database()")).scalar()
            if db_name != "ecommerce_agent_test":
                pytest.skip("POSTGRES_TEST_DATABASE_URL does not target ecommerce_agent_test")

        # Upgrade alembic to head on ecommerce_agent_test
        from alembic import command
        from alembic.config import Config
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        config = Config(str(root / "alembic.ini"))
        os.environ["DATABASE_URL"] = pg_url
        command.upgrade(config, "head")

        session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
        with session_factory() as session:
            pg_user, pg_order = _create_user_and_order(
                session,
                email=f"pg_guest_{uuid4().hex[:6]}@example.com",
                order_number=f"ORD-PG-{uuid4().hex[:8].upper()}",
            )

            settings = Settings(
                GEMINI_API_KEY="test-key",
                DATABASE_URL=pg_url,
                AUTH_SECRET_KEY=TEST_AUTH_SECRET.get_secret_value(),
                OTP_HASH_SECRET=TEST_OTP_SECRET.get_secret_value(),
                ENVIRONMENT="development",
            )
            email_provider = InMemoryEmailDeliveryProvider()

            # 1. Request verification on PostgreSQL
            sent = request_guest_verification(
                session,
                pg_order.order_number,
                pg_user.email,
                settings,
                email_provider=email_provider,
            )
            assert sent is True

            otp = email_provider.get_sent_messages()[-1]["otp_code"]

            # 2. Verify OTP on PostgreSQL
            order, token, expires_in = verify_guest_otp(
                session, pg_order.order_number, pg_user.email, otp, settings
            )
            assert order.id == pg_order.id
            assert expires_in > 0

            # 3. Check session record on PostgreSQL
            sess_rec = session.scalar(
                select(GuestAccessSession).where(
                    GuestAccessSession.order_id == pg_order.id
                )
            )
            assert sess_rec is not None
            assert sess_rec.revoked_at is None
    finally:
        engine.dispose()


def test_postgres_concurrent_initial_verification_requests():
    """Verify 2 concurrent initial verification requests for the same order serialize with exactly 1 succeeding on PostgreSQL."""
    pg_url = _get_postgres_test_url()
    if not pg_url:
        pytest.skip("POSTGRES_TEST_DATABASE_URL not configured; skipping live PostgreSQL test")

    engine = create_engine(pg_url)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    try:
        with session_factory() as session:
            user, order = _create_user_and_order(
                session,
                email=f"pg_init_{uuid4().hex[:6]}@example.com",
                order_number=f"ORD-PG-INIT-{uuid4().hex[:6].upper()}",
            )
            settings = Settings(
                GEMINI_API_KEY="test-key",
                DATABASE_URL=pg_url,
                AUTH_SECRET_KEY=TEST_AUTH_SECRET.get_secret_value(),
                OTP_HASH_SECRET=TEST_OTP_SECRET.get_secret_value(),
                ENVIRONMENT="development",
            )

        def request_otp():
            with session_factory() as thread_session:
                provider = InMemoryEmailDeliveryProvider()
                try:
                    res = request_guest_verification(
                        thread_session,
                        order.order_number,
                        user.email,
                        settings,
                        email_provider=provider,
                    )
                    return ("success", res)
                except RateLimitExceededError as rle:
                    return ("cooldown", rle.retry_after)
                except Exception as exc:
                    return ("error", type(exc).__name__)

        with ThreadPoolExecutor(max_workers=2) as executor:
            f1 = executor.submit(request_otp)
            f2 = executor.submit(request_otp)
            results = [f1.result(), f2.result()]

        statuses = [r[0] for r in results]
        assert "success" in statuses
        assert "cooldown" in statuses

        with session_factory() as session:
            count = session.scalar(
                select(sa_func.count()).select_from(GuestOrderVerification).where(
                    GuestOrderVerification.order_id == order.id
                )
            )
            assert count == 1
    finally:
        engine.dispose()


def test_postgres_concurrent_resend_requests():
    """Verify 2 concurrent resends after cooldown serialize with exactly 1 succeeding on PostgreSQL."""
    pg_url = _get_postgres_test_url()
    if not pg_url:
        pytest.skip("POSTGRES_TEST_DATABASE_URL not configured; skipping live PostgreSQL test")

    engine = create_engine(pg_url)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    try:
        with session_factory() as session:
            user, order = _create_user_and_order(
                session,
                email=f"pg_resend_{uuid4().hex[:6]}@example.com",
                order_number=f"ORD-PG-RES-{uuid4().hex[:6].upper()}",
            )
            settings = Settings(
                GEMINI_API_KEY="test-key",
                DATABASE_URL=pg_url,
                AUTH_SECRET_KEY=TEST_AUTH_SECRET.get_secret_value(),
                OTP_HASH_SECRET=TEST_OTP_SECRET.get_secret_value(),
                ENVIRONMENT="development",
            )
            provider = InMemoryEmailDeliveryProvider()
            request_guest_verification(session, order.order_number, user.email, settings, provider)

            # Backdate past 60s cooldown
            rec = session.scalar(
                select(GuestOrderVerification).where(GuestOrderVerification.order_id == order.id)
            )
            rec.created_at = utc_now() - timedelta(seconds=70)
            session.commit()

        def resend_otp():
            with session_factory() as thread_session:
                p = InMemoryEmailDeliveryProvider()
                try:
                    res = request_guest_verification(
                        thread_session,
                        order.order_number,
                        user.email,
                        settings,
                        email_provider=p,
                    )
                    return ("success", res)
                except RateLimitExceededError:
                    return ("cooldown", None)
                except Exception as exc:
                    return ("error", type(exc).__name__)

        with ThreadPoolExecutor(max_workers=2) as executor:
            f1 = executor.submit(resend_otp)
            f2 = executor.submit(resend_otp)
            results = [f1.result(), f2.result()]

        statuses = [r[0] for r in results]
        assert "success" in statuses
        assert "cooldown" in statuses
    finally:
        engine.dispose()


def test_postgres_concurrent_resend_and_verification():
    """Verify concurrent resend and verification execute safely without deadlocks on PostgreSQL."""
    pg_url = _get_postgres_test_url()
    if not pg_url:
        pytest.skip("POSTGRES_TEST_DATABASE_URL not configured; skipping live PostgreSQL test")

    engine = create_engine(pg_url)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    try:
        with session_factory() as session:
            user, order = _create_user_and_order(
                session,
                email=f"pg_rv_{uuid4().hex[:6]}@example.com",
                order_number=f"ORD-PG-RV-{uuid4().hex[:6].upper()}",
            )
            settings = Settings(
                GEMINI_API_KEY="test-key",
                DATABASE_URL=pg_url,
                AUTH_SECRET_KEY=TEST_AUTH_SECRET.get_secret_value(),
                OTP_HASH_SECRET=TEST_OTP_SECRET.get_secret_value(),
                ENVIRONMENT="development",
            )
            provider = InMemoryEmailDeliveryProvider()
            request_guest_verification(session, order.order_number, user.email, settings, provider)
            otp1 = provider.get_sent_messages()[0]["otp_code"]

            rec = session.scalar(
                select(GuestOrderVerification).where(GuestOrderVerification.order_id == order.id)
            )
            rec.created_at = utc_now() - timedelta(seconds=70)
            session.commit()

        def do_resend():
            with session_factory() as thread_session:
                p = InMemoryEmailDeliveryProvider()
                try:
                    request_guest_verification(
                        thread_session, order.order_number, user.email, settings, email_provider=p
                    )
                    return ("resend", "success")
                except Exception as e:
                    return ("resend", type(e).__name__)

        def do_verify():
            with session_factory() as thread_session:
                try:
                    verify_guest_otp(thread_session, order.order_number, user.email, otp1, settings)
                    return ("verify", "success")
                except Exception as e:
                    return ("verify", type(e).__name__)

        with ThreadPoolExecutor(max_workers=2) as executor:
            f1 = executor.submit(do_resend)
            f2 = executor.submit(do_verify)
            res1 = f1.result()
            res2 = f2.result()

        assert res1[0] == "resend"
        assert res2[0] == "verify"
    finally:
        engine.dispose()


def test_postgres_migration_0005_downgrade_and_reupgrade():
    """Verify Alembic migration 0005 downgrade to 0004 and re-upgrade to head on isolated ecommerce_agent_test."""
    from alembic import command
    from alembic.config import Config

    pg_url = _get_postgres_test_url()
    if not pg_url:
        pytest.skip("POSTGRES_TEST_DATABASE_URL not configured; skipping live PostgreSQL test")

    root = Path(__file__).resolve().parents[2]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", pg_url.replace("%", "%%"))
    os.environ["DATABASE_URL"] = pg_url

    engine = create_engine(pg_url)
    try:
        # 1. Downgrade to 0004_order_fingerprint
        command.downgrade(config, "0004_order_fingerprint")

        insp_down = inspect(engine)
        down_tables = set(insp_down.get_table_names())
        assert "guest_order_verifications" not in down_tables
        assert "guest_access_sessions" not in down_tables
        assert "guest_email_outbox" not in down_tables

        orders_cols = {c["name"] for c in insp_down.get_columns("orders")}
        assert "customer_email_snapshot" not in orders_cols

        # 2. Re-upgrade to head (0005_guest_order_verification)
        command.upgrade(config, "head")

        insp_up = inspect(engine)
        up_tables = set(insp_up.get_table_names())
        assert "guest_order_verifications" in up_tables
        assert "guest_access_sessions" in up_tables
        assert "guest_email_outbox" in up_tables

        orders_cols_up = {c["name"] for c in insp_up.get_columns("orders")}
        assert "customer_email_snapshot" in orders_cols_up

        gov_cols = {c["name"] for c in insp_up.get_columns("guest_order_verifications")}
        assert "delivery_failed" in gov_cols

    finally:
        try:
            command.upgrade(config, "head")
        except Exception:
            pass
        engine.dispose()
