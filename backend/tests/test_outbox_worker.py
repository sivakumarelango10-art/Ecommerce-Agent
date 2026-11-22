import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import logging
import os
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from pydantic import SecretStr
import pytest
from sqlalchemy import create_engine, event, func as sa_func, select, text
from sqlalchemy.orm import Session, sessionmaker

from backend.app.core.config import Settings
from backend.app.core.datetime_utils import ensure_utc, utc_now
from backend.app.db.base import Base
from backend.app.db.models import (
    Address,
    GuestAccessSession,
    GuestEmailOutbox,
    GuestOrderVerification,
    Order,
    OrderItem,
    OrderStatus,
    OutboxDeliveryStatus,
    User,
)
from backend.app.main import create_app
from backend.app.security.otp_encryption import encrypt_otp
from backend.app.security.passwords import hash_password
from backend.app.services.email_service import (
    EmailDeliveryError,
    InMemoryEmailDeliveryProvider,
    set_email_provider,
)
from backend.app.services.guest_verification_service import (
    claim_outbox_jobs,
    generate_otp,
    hash_otp,
    record_outbox_failure,
    record_outbox_success,
    request_guest_verification,
)
from backend.app.services.outbox_worker import GuestEmailOutboxWorker
from backend.app.services.shipping_tax_policy import (
    ConfigurableFlatShippingPolicy,
    ConfigurablePercentageTaxPolicy,
    set_shipping_policy,
    set_tax_policy,
)

TEST_OTP_SECRET = SecretStr("test-otp-secret-key-at-least-16-bytes")
TEST_AUTH_SECRET = SecretStr("test-auth-secret-key-at-least-32-bytes-long")


@pytest.fixture(autouse=True)
def reset_providers():
    in_memory = InMemoryEmailDeliveryProvider()
    set_email_provider(in_memory)
    set_shipping_policy(ConfigurableFlatShippingPolicy(Decimal("50.00")))
    set_tax_policy(ConfigurablePercentageTaxPolicy(Decimal("18.00")))
    yield in_memory
    set_email_provider(None)
    set_shipping_policy(None)
    set_tax_policy(None)


def _create_test_order(session: Session, email: str = "outbox_test@example.com") -> Order:
    user = User(
        id=uuid4(),
        email=email,
        password_hash=hash_password("Pass123!"),
        is_active=True,
    )
    session.add(user)
    session.flush()

    addr = Address(
        id=uuid4(),
        user_id=user.id,
        recipient_name="Outbox Tester",
        line1="123 Worker St",
        city="Bengaluru",
        region="KA",
        postal_code="560001",
        country_code="IN",
    )
    session.add(addr)
    session.flush()

    order = Order(
        id=uuid4(),
        order_number=f"ORD-OUTBOX-{uuid4().hex[:6].upper()}",
        user_id=user.id,
        customer_email_snapshot=email,
        shipping_address_id=addr.id,
        shipping_recipient_name=addr.recipient_name,
        shipping_line1=addr.line1,
        shipping_city=addr.city,
        shipping_region=addr.region,
        shipping_postal_code=addr.postal_code,
        shipping_country_code=addr.country_code,
        status=OrderStatus.CONFIRMED.value,
        currency="INR",
        subtotal=Decimal("100.00"),
        shipping_fee=Decimal("50.00"),
        tax=Decimal("18.00"),
        total_amount=Decimal("168.00"),
    )
    session.add(order)
    session.commit()
    session.refresh(order)
    return order


def _seed_pending_outbox_job(
    session: Session,
    order: Order,
    settings: Settings,
    otp_code: str = "123456",
    next_retry_at: datetime | None = None,
    attempts: int = 0,
) -> tuple[GuestOrderVerification, GuestEmailOutbox]:
    now = utc_now()
    otp_digest = hash_otp(otp_code, settings.otp_hash_secret)
    encrypted_payload = encrypt_otp(otp_code, settings.otp_hash_secret)

    ver = GuestOrderVerification(
        id=uuid4(),
        order_id=order.id,
        email_snapshot=order.customer_email_snapshot,
        otp_hash=otp_digest,
        expires_at=now + timedelta(minutes=5),
        attempt_count=0,
        resend_count=0,
        delivery_failed=False,
        created_at=now,
    )
    session.add(ver)
    session.flush()

    job = GuestEmailOutbox(
        id=uuid4(),
        order_id=order.id,
        verification_id=ver.id,
        recipient_email=order.customer_email_snapshot,
        order_number=order.order_number,
        encrypted_otp_payload=encrypted_payload,
        status=OutboxDeliveryStatus.PENDING.value if attempts == 0 else OutboxDeliveryStatus.RETRY.value,
        attempts=attempts,
        max_attempts=3,
        next_retry_at=next_retry_at,
        created_at=now,
        updated_at=now,
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    return ver, job


# ==============================================================================
# 1. Automatic Processing During Application Runtime & Lifespan
# ==============================================================================

@pytest.mark.anyio
async def test_worker_automatic_processing_during_runtime(tmp_path):
    """Verify background worker processes queued jobs automatically during FastAPI application lifespan."""
    db_file = (tmp_path / "runtime_outbox.sqlite").as_posix()
    db_url = f"sqlite+pysqlite:///{db_file}"
    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    settings = Settings(
        GEMINI_API_KEY="test-key",
        DATABASE_URL=db_url,
        AUTH_SECRET_KEY=TEST_AUTH_SECRET.get_secret_value(),
        OTP_HASH_SECRET=TEST_OTP_SECRET.get_secret_value(),
        ENVIRONMENT="development",
        OUTBOX_WORKER_ENABLED=True,
        OUTBOX_POLL_INTERVAL_SECONDS=0.1,  # Fast poll for testing
        OUTBOX_LEASE_SECONDS=30,
    )

    in_memory = InMemoryEmailDeliveryProvider()

    app = create_app(
        settings_loader=lambda: settings,
        engine_factory=lambda url: engine,
        email_provider=in_memory,
        outbox_worker_enabled=True,
    )

    with session_factory() as session:
        order = _create_test_order(session)
        # Seed an un-dispatched pending job directly
        _seed_pending_outbox_job(session, order, settings, otp_code="789012")

    # Start FastAPI application lifespan with TestClient
    with TestClient(app) as client:
        assert client.app.state.outbox_worker is not None
        assert client.app.state.outbox_worker.is_running is True

        # Wait for the background worker to poll and process
        for _ in range(30):
            await asyncio.sleep(0.1)
            with session_factory() as session:
                job = session.scalar(select(GuestEmailOutbox).where(GuestEmailOutbox.order_id == order.id))
                if job and job.status == OutboxDeliveryStatus.SENT.value:
                    break

        with session_factory() as session:
            job = session.scalar(select(GuestEmailOutbox).where(GuestEmailOutbox.order_id == order.id))
            assert job.status == OutboxDeliveryStatus.SENT.value
            assert job.sent_at is not None
            assert job.encrypted_otp_payload is None  # scrubbed

        messages = in_memory.get_sent_messages()
        assert len(messages) == 1
        assert messages[0]["order_number"] == order.order_number
        assert messages[0]["otp_code"] == "789012"

    engine.dispose()

# ==============================================================================
# 2. Recovery After Restart
# ==============================================================================

@pytest.mark.anyio
async def test_worker_recovery_after_restart(tmp_path):
    """Verify worker sweeps and delivers stranded pending and expired-lease jobs immediately upon startup."""
    db_file = (tmp_path / "restart_outbox.sqlite").as_posix()
    db_url = f"sqlite+pysqlite:///{db_file}"
    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    settings = Settings(
        GEMINI_API_KEY="test-key",
        DATABASE_URL=db_url,
        AUTH_SECRET_KEY=TEST_AUTH_SECRET.get_secret_value(),
        OTP_HASH_SECRET=TEST_OTP_SECRET.get_secret_value(),
        ENVIRONMENT="development",
    )

    in_memory = InMemoryEmailDeliveryProvider()

    # Seed 2 stranded jobs before starting any worker:
    # 1: Pure pending job
    # 2: Job whose previous lease expired (simulating worker crash mid-send)
    with session_factory() as session:
        order1 = _create_test_order(session, email="stranded1@example.com")
        order2 = _create_test_order(session, email="stranded2@example.com")

        _seed_pending_outbox_job(session, order1, settings, otp_code="111111")
        _seed_pending_outbox_job(
            session,
            order2,
            settings,
            otp_code="222222",
            next_retry_at=utc_now() - timedelta(seconds=5),  # expired lease
            attempts=1,
        )

    # Launch worker (simulating server boot)
    worker = GuestEmailOutboxWorker(
        session_factory=session_factory,
        settings=settings,
        email_provider=in_memory,
        poll_interval_seconds=0.1,
    )
    await worker.start()

    # Wait for recovery sweep
    for _ in range(30):
        await asyncio.sleep(0.1)
        with session_factory() as session:
            jobs = list(session.scalars(select(GuestEmailOutbox)).all())
            if all(j.status == OutboxDeliveryStatus.SENT.value for j in jobs):
                break

    await worker.stop()

    with session_factory() as session:
        jobs = list(session.scalars(select(GuestEmailOutbox)).all())
        assert len(jobs) == 2
        for j in jobs:
            assert j.status == OutboxDeliveryStatus.SENT.value
            assert j.encrypted_otp_payload is None

    sent_codes = {m["otp_code"] for m in in_memory.get_sent_messages()}
    assert sent_codes == {"111111", "222222"}

    engine.dispose()

# ==============================================================================
# 3. Multiple Workers Claiming Jobs Concurrently
# ==============================================================================

def test_multiple_workers_claiming_jobs_concurrently(tmp_path):
    """Verify concurrent worker instances process disjoint jobs without duplicate deliveries."""
    db_file = (tmp_path / "multi_worker.sqlite").as_posix()
    db_url = f"sqlite+pysqlite:///{db_file}"
    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    settings = Settings(
        GEMINI_API_KEY="test-key",
        DATABASE_URL=db_url,
        AUTH_SECRET_KEY=TEST_AUTH_SECRET.get_secret_value(),
        OTP_HASH_SECRET=TEST_OTP_SECRET.get_secret_value(),
        ENVIRONMENT="development",
    )

    in_memory = InMemoryEmailDeliveryProvider()

    # Seed 6 pending jobs
    with session_factory() as session:
        for i in range(6):
            order = _create_test_order(session, email=f"user_{i}@example.com")
            _seed_pending_outbox_job(session, order, settings, otp_code=f"{100000 + i}")

    # Create 3 concurrent worker instances
    workers = [
        GuestEmailOutboxWorker(
            session_factory=session_factory,
            settings=settings,
            email_provider=in_memory,
            batch_size=2,
            lease_seconds=30,
        )
        for _ in range(3)
    ]

    def run_worker_sweep(w: GuestEmailOutboxWorker):
        total = 0
        for _ in range(3):
            total += w.process_batch()
            if total >= 2:
                break
        return total

    with ThreadPoolExecutor(max_workers=3) as executor:
        results = list(executor.map(run_worker_sweep, workers))

    # All jobs processed
    with session_factory() as session:
        jobs = list(session.scalars(select(GuestEmailOutbox)).all())
        sent_count = sum(1 for j in jobs if j.status == OutboxDeliveryStatus.SENT.value)
        assert sent_count == 6

    # Verify zero duplicate sends
    messages = in_memory.get_sent_messages()
    assert len(messages) == 6
    unique_otps = {m["otp_code"] for m in messages}
    assert len(unique_otps) == 6

    engine.dispose()

# ==============================================================================
# 4. Graceful Shutdown
# ==============================================================================

@pytest.mark.anyio
async def test_worker_graceful_shutdown(tmp_path):
    """Verify worker starts, runs, and stops gracefully without leaking unhandled exceptions."""
    db_file = (tmp_path / "shutdown_worker.sqlite").as_posix()
    db_url = f"sqlite+pysqlite:///{db_file}"
    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    settings = Settings(
        GEMINI_API_KEY="test-key",
        DATABASE_URL=db_url,
        AUTH_SECRET_KEY=TEST_AUTH_SECRET.get_secret_value(),
        OTP_HASH_SECRET=TEST_OTP_SECRET.get_secret_value(),
        ENVIRONMENT="development",
    )

    worker = GuestEmailOutboxWorker(
        session_factory=session_factory,
        settings=settings,
        poll_interval_seconds=0.05,
    )
    assert worker.is_running is False

    await worker.start()
    assert worker.is_running is True

    # Allow it to run a brief moment
    await asyncio.sleep(0.1)

    # Graceful stop
    await worker.stop(timeout=2.0)
    assert worker.is_running is False
    assert worker._task is None

    engine.dispose()

# ==============================================================================
# 5. SMTP Failures and Retry Behavior
# ==============================================================================

def test_worker_smtp_failure_and_retry_backoff(tmp_path):
    """Verify worker records temporary delivery failure with backoff and does not drop the job."""
    db_file = (tmp_path / "failure_worker.sqlite").as_posix()
    db_url = f"sqlite+pysqlite:///{db_file}"
    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    settings = Settings(
        GEMINI_API_KEY="test-key",
        DATABASE_URL=db_url,
        AUTH_SECRET_KEY=TEST_AUTH_SECRET.get_secret_value(),
        OTP_HASH_SECRET=TEST_OTP_SECRET.get_secret_value(),
        ENVIRONMENT="development",
    )

    class FailingProvider:
        def send_otp_email(self, recipient, order_number, otp_code):
            raise EmailDeliveryError("SMTP connection reset by peer")

    with session_factory() as session:
        order = _create_test_order(session)
        _, job = _seed_pending_outbox_job(session, order, settings, otp_code="555555")

    worker = GuestEmailOutboxWorker(
        session_factory=session_factory,
        settings=settings,
        email_provider=FailingProvider(),
    )

    # Run sweep: should catch error and schedule retry
    success = worker.process_batch()
    assert success == 0

    with session_factory() as session:
        reloaded = session.scalar(select(GuestEmailOutbox).where(GuestEmailOutbox.id == job.id))
        assert reloaded.status == OutboxDeliveryStatus.RETRY.value
        assert reloaded.attempts == 1
        assert reloaded.next_retry_at is not None
        assert ensure_utc(reloaded.next_retry_at) > utc_now()
        assert "SMTP connection reset" in reloaded.last_error
        # Payload preserved for retry
        assert reloaded.encrypted_otp_payload is not None

    engine.dispose()

def test_worker_permanent_failure_invalidates_otp_and_scrubs_payload(tmp_path):
    """Verify 3 failed attempts result in permanent failure, payload scrubbing, and OTP invalidation."""
    db_file = (tmp_path / "perm_fail_worker.sqlite").as_posix()
    db_url = f"sqlite+pysqlite:///{db_file}"
    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    settings = Settings(
        GEMINI_API_KEY="test-key",
        DATABASE_URL=db_url,
        AUTH_SECRET_KEY=TEST_AUTH_SECRET.get_secret_value(),
        OTP_HASH_SECRET=TEST_OTP_SECRET.get_secret_value(),
        ENVIRONMENT="development",
    )

    class PermanentFailingProvider:
        def send_otp_email(self, recipient, order_number, otp_code):
            raise EmailDeliveryError("550 No such user here")

    with session_factory() as session:
        order = _create_test_order(session)
        # Seed with attempts=2 so next failure hits max_attempts=3
        ver, job = _seed_pending_outbox_job(
            session, order, settings, otp_code="666666", attempts=2
        )

    worker = GuestEmailOutboxWorker(
        session_factory=session_factory,
        settings=settings,
        email_provider=PermanentFailingProvider(),
    )

    worker.process_batch()

    with session_factory() as session:
        reloaded_job = session.scalar(select(GuestEmailOutbox).where(GuestEmailOutbox.id == job.id))
        assert reloaded_job.status == OutboxDeliveryStatus.FAILED.value
        assert reloaded_job.attempts == 3
        assert reloaded_job.encrypted_otp_payload is None  # scrubbed

        reloaded_ver = session.scalar(select(GuestOrderVerification).where(GuestOrderVerification.id == ver.id))
        assert reloaded_ver.consumed_at is not None  # invalidated
        assert reloaded_ver.delivery_failed is True

    engine.dispose()

# ==============================================================================
# 6. No Plaintext OTP in Database or Logs
# ==============================================================================

def test_worker_no_plaintext_otp_in_database_or_logs(tmp_path, caplog):
    """Verify zero plaintext OTP appears in database columns or log stream during worker execution."""
    db_file = (tmp_path / "otp_leak_worker.sqlite").as_posix()
    db_url = f"sqlite+pysqlite:///{db_file}"
    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    settings = Settings(
        GEMINI_API_KEY="test-key",
        DATABASE_URL=db_url,
        AUTH_SECRET_KEY=TEST_AUTH_SECRET.get_secret_value(),
        OTP_HASH_SECRET=TEST_OTP_SECRET.get_secret_value(),
        ENVIRONMENT="development",
    )

    in_memory = InMemoryEmailDeliveryProvider()
    secret_otp = "987654"

    with session_factory() as session:
        order = _create_test_order(session)
        _seed_pending_outbox_job(session, order, settings, otp_code=secret_otp)

    worker = GuestEmailOutboxWorker(
        session_factory=session_factory,
        settings=settings,
        email_provider=in_memory,
    )

    with caplog.at_level(logging.DEBUG):
        worker.process_batch()

    # 1. Check logs
    for record in caplog.records:
        assert secret_otp not in record.getMessage()

    # 2. Check all tables
    with session_factory() as session:
        for model_cls in [GuestEmailOutbox, GuestOrderVerification, GuestAccessSession, Order]:
            for row in session.scalars(select(model_cls)).all():
                for col in model_cls.__table__.columns:
                    val = getattr(row, col.name)
                    if val is not None:
                        assert secret_otp not in str(val)

    engine.dispose()

# ==============================================================================
# 7. PostgreSQL Live Concurrency Test (using ecommerce_agent_test only)
# ==============================================================================

def _get_postgres_test_url() -> str | None:
    from backend.tests.test_checkout_concurrency_postgres import _get_postgres_test_url as get_url
    return get_url()


def test_worker_postgres_concurrent_workers_no_duplicates():
    """Verify multiple worker instances claiming jobs concurrently on live PostgreSQL with FOR UPDATE SKIP LOCKED."""
    pg_url = _get_postgres_test_url()
    if not pg_url:
        pytest.skip("POSTGRES_TEST_DATABASE_URL not configured")

    engine = create_engine(pg_url)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    settings = Settings(
        GEMINI_API_KEY="test-key",
        DATABASE_URL=pg_url,
        AUTH_SECRET_KEY=TEST_AUTH_SECRET.get_secret_value(),
        OTP_HASH_SECRET=TEST_OTP_SECRET.get_secret_value(),
        ENVIRONMENT="development",
    )

    in_memory = InMemoryEmailDeliveryProvider()
    seeded_order_ids = []

    try:
        # Seed 8 pending jobs on PostgreSQL
        with session_factory() as session:
            for i in range(8):
                order = _create_test_order(session, email=f"pg_worker_{uuid4().hex[:6]}@example.com")
                seeded_order_ids.append(order.id)
                _seed_pending_outbox_job(session, order, settings, otp_code=f"{200000 + i}")

        # 4 concurrent workers sweeping 2 jobs each
        workers = [
            GuestEmailOutboxWorker(
                session_factory=session_factory,
                settings=settings,
                email_provider=in_memory,
                batch_size=2,
                lease_seconds=30,
            )
            for _ in range(4)
        ]

        def worker_task(w: GuestEmailOutboxWorker):
            return w.process_batch()

        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(worker_task, workers))

        assert sum(results) == 8

        # Verify all 8 jobs are sent and encrypted payload scrubbed
        with session_factory() as session:
            jobs = list(
                session.scalars(
                    select(GuestEmailOutbox).where(GuestEmailOutbox.order_id.in_(seeded_order_ids))
                ).all()
            )
            assert len(jobs) == 8
            for j in jobs:
                assert j.status == OutboxDeliveryStatus.SENT.value
                assert j.encrypted_otp_payload is None

        # Verify exactly 8 emails sent, 0 duplicates
        messages = [m for m in in_memory.get_sent_messages() if m["order_number"].startswith("ORD-OUTBOX")]
        assert len(messages) == 8
        otps = {m["otp_code"] for m in messages}
        assert len(otps) == 8

    finally:
        engine.dispose()

# ==============================================================================
# 8. Targeted Lease Fencing and Stale Worker Tests
# ==============================================================================

def test_stale_worker_token_mismatch_and_reclaim_isolation(tmp_path):
    """Verify stale worker finalization is rejected when a job is reclaimed with a new token."""
    db_file = (tmp_path / "fencing_mismatch.sqlite").as_posix()
    db_url = f"sqlite+pysqlite:///{db_file}"
    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    settings = Settings(
        GEMINI_API_KEY="test-key",
        DATABASE_URL=db_url,
        AUTH_SECRET_KEY=TEST_AUTH_SECRET.get_secret_value(),
        OTP_HASH_SECRET=TEST_OTP_SECRET.get_secret_value(),
        ENVIRONMENT="development",
    )

    with session_factory() as session:
        order = _create_test_order(session)
        ver, job = _seed_pending_outbox_job(session, order, settings, otp_code="123456")

        # 1. Worker A claims the job
        claims_a = claim_outbox_jobs(session, limit=1, lease_seconds=30)
        assert len(claims_a) == 1
        claim_a = claims_a[0]
        token_a = claim_a["lease_token"]
        assert token_a is not None

        # 2. Simulate Worker A lease expiration
        past_time = utc_now() - timedelta(seconds=1)
        reloaded = session.scalar(select(GuestEmailOutbox).where(GuestEmailOutbox.id == job.id))
        reloaded.next_retry_at = past_time
        session.commit()

        # 3. Worker B reclaims the job with a new token
        claims_b = claim_outbox_jobs(session, limit=1, lease_seconds=30)
        assert len(claims_b) == 1
        claim_b = claims_b[0]
        token_b = claim_b["lease_token"]
        assert token_b is not None
        assert token_a != token_b

        # 4. Worker A attempts success finalization -> Rejected
        success_a = record_outbox_success(session, job.id, token_a)
        assert success_a is False

        # Verify job is still unfinalized and owned by Worker B
        reloaded_b = session.scalar(select(GuestEmailOutbox).where(GuestEmailOutbox.id == job.id))
        assert reloaded_b.status == OutboxDeliveryStatus.PENDING.value
        assert reloaded_b.lease_token == token_b
        assert reloaded_b.encrypted_otp_payload is not None

        # 5. Worker A attempts failure finalization -> Rejected
        failure_a = record_outbox_failure(
            session, job.id, ver.id, token_a, Exception("Stale Worker A error")
        )
        assert failure_a is False

        # Verify Worker B's lease, deadline, and attempt count are completely untouched
        session.refresh(reloaded_b)
        assert reloaded_b.attempts == 0
        assert reloaded_b.last_error is None
        assert reloaded_b.lease_token == token_b
        assert reloaded_b.status == OutboxDeliveryStatus.PENDING.value

        # Verification record must not be touched
        reloaded_ver = session.scalar(select(GuestOrderVerification).where(GuestOrderVerification.id == ver.id))
        assert reloaded_ver.consumed_at is None
        assert reloaded_ver.delivery_failed is False

        # 6. Current owner (Worker B) successfully finalizes
        success_b = record_outbox_success(session, job.id, token_b)
        assert success_b is True

        session.refresh(reloaded_b)
        assert reloaded_b.status == OutboxDeliveryStatus.SENT.value
        assert reloaded_b.sent_at is not None
        assert reloaded_b.lease_token is None  # cleared on terminal state
        assert reloaded_b.encrypted_otp_payload is None  # scrubbed

    engine.dispose()

def test_expired_lease_cannot_finalize_even_if_unclaimed(tmp_path):
    """Verify a worker whose lease expired cannot finalize even if no other worker has claimed the job yet."""
    db_file = (tmp_path / "fencing_expired.sqlite").as_posix()
    db_url = f"sqlite+pysqlite:///{db_file}"
    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    settings = Settings(
        GEMINI_API_KEY="test-key",
        DATABASE_URL=db_url,
        AUTH_SECRET_KEY=TEST_AUTH_SECRET.get_secret_value(),
        OTP_HASH_SECRET=TEST_OTP_SECRET.get_secret_value(),
        ENVIRONMENT="development",
    )

    with session_factory() as session:
        order = _create_test_order(session)
        ver, job = _seed_pending_outbox_job(session, order, settings, otp_code="654321")

        # Claim job
        claims = claim_outbox_jobs(session, limit=1, lease_seconds=30)
        assert len(claims) == 1
        claim = claims[0]
        token = claim["lease_token"]

        # Expire the lease in DB without another worker claiming it
        job_in_db = session.scalar(select(GuestEmailOutbox).where(GuestEmailOutbox.id == job.id))
        job_in_db.next_retry_at = utc_now() - timedelta(seconds=2)
        session.commit()

        # Attempt success finalization after expiry -> Rejected
        success = record_outbox_success(session, job.id, token)
        assert success is False

        # Attempt failure finalization after expiry -> Rejected
        failure = record_outbox_failure(
            session, job.id, ver.id, token, Exception("Timeout after expiry")
        )
        assert failure is False

        # Verify job remains pending and can be picked up by a new claim
        session.refresh(job_in_db)
        assert job_in_db.status == OutboxDeliveryStatus.PENDING.value
        assert job_in_db.attempts == 0
        assert job_in_db.encrypted_otp_payload is not None

        # A new claim succeeds and issues a fresh token
        new_claims = claim_outbox_jobs(session, limit=1, lease_seconds=30)
        assert len(new_claims) == 1
        new_token = new_claims[0]["lease_token"]
        assert new_token != token

        # New owner successfully finalizes
        assert record_outbox_success(session, job.id, new_token) is True

    engine.dispose()

def test_failure_retry_clears_lease_token_until_next_claim(tmp_path):
    """Verify recording temporary failure schedules retry, clears lease_token, and preserves payload."""
    db_file = (tmp_path / "fencing_retry.sqlite").as_posix()
    db_url = f"sqlite+pysqlite:///{db_file}"
    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    settings = Settings(
        GEMINI_API_KEY="test-key",
        DATABASE_URL=db_url,
        AUTH_SECRET_KEY=TEST_AUTH_SECRET.get_secret_value(),
        OTP_HASH_SECRET=TEST_OTP_SECRET.get_secret_value(),
        ENVIRONMENT="development",
    )

    with session_factory() as session:
        order = _create_test_order(session)
        ver, job = _seed_pending_outbox_job(session, order, settings, otp_code="777777")

        claims = claim_outbox_jobs(session, limit=1, lease_seconds=30)
        claim = claims[0]
        token = claim["lease_token"]

        # Record temporary failure with active lease
        result = record_outbox_failure(
            session, job.id, ver.id, token, Exception("Temporary network glitch")
        )
        assert result is True

        reloaded = session.scalar(select(GuestEmailOutbox).where(GuestEmailOutbox.id == job.id))
        assert reloaded.status == OutboxDeliveryStatus.RETRY.value
        assert reloaded.attempts == 1
        assert reloaded.lease_token is None  # cleared while waiting for retry
        assert reloaded.next_retry_at is not None
        assert reloaded.encrypted_otp_payload is not None

        # Stale call with old token cannot touch it
        stale_call = record_outbox_success(session, job.id, token)
        assert stale_call is False

    engine.dispose()

def test_postgres_concurrent_workers_fencing_tokens_no_corruption():
    """Verify live PostgreSQL workers assign disjoint UUID fencing tokens and reject stale token operations."""
    pg_url = _get_postgres_test_url()
    if not pg_url:
        pytest.skip("POSTGRES_TEST_DATABASE_URL not configured")

    engine = create_engine(pg_url)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    settings = Settings(
        GEMINI_API_KEY="test-key",
        DATABASE_URL=pg_url,
        AUTH_SECRET_KEY=TEST_AUTH_SECRET.get_secret_value(),
        OTP_HASH_SECRET=TEST_OTP_SECRET.get_secret_value(),
        ENVIRONMENT="development",
    )

    seeded_order_ids = []
    job_ids = []

    try:
        with session_factory() as session:
            for i in range(6):
                order = _create_test_order(session, email=f"fencing_pg_{uuid4().hex[:6]}@example.com")
                seeded_order_ids.append(order.id)
                _, j = _seed_pending_outbox_job(session, order, settings, otp_code=f"{300000 + i}")
                job_ids.append(j.id)

        # 3 concurrent workers claim 2 jobs each
        def claim_worker():
            with session_factory() as s:
                return claim_outbox_jobs(s, limit=2, lease_seconds=30)

        with ThreadPoolExecutor(max_workers=3) as executor:
            claimed_batches = list(executor.map(lambda _: claim_worker(), range(3)))

        all_claimed = [item for batch in claimed_batches for item in batch]
        assert len(all_claimed) == 6

        # Verify all tokens are unique and non-null
        tokens = [item["lease_token"] for item in all_claimed]
        assert len(set(tokens)) == 6
        assert all(t is not None for t in tokens)

        # For every job, verify an unauthorized fake token is rejected
        with session_factory() as session:
            for item in all_claimed:
                fake_token = uuid4()
                rejected_success = record_outbox_success(session, item["id"], fake_token)
                assert rejected_success is False

                rejected_failure = record_outbox_failure(
                    session, item["id"], item["verification_id"], fake_token, Exception("Fake token error")
                )
                assert rejected_failure is False

        # Now finalize all jobs with their valid owner tokens
        with session_factory() as session:
            for item in all_claimed:
                valid_success = record_outbox_success(session, item["id"], item["lease_token"])
                assert valid_success is True

        # Verify in DB: all 6 are SENT, lease_token is NULL, payload is scrubbed
        with session_factory() as session:
            jobs = list(
                session.scalars(
                    select(GuestEmailOutbox).where(GuestEmailOutbox.id.in_(job_ids))
                ).all()
            )
            assert len(jobs) == 6
            for j in jobs:
                assert j.status == OutboxDeliveryStatus.SENT.value
                assert j.lease_token is None
                assert j.encrypted_otp_payload is None
                assert j.sent_at is not None

    finally:
        engine.dispose()

def test_exact_boundary_lease_deadline_rejects_success_and_failure(tmp_path, monkeypatch):
    """Verify that when current time equals the lease deadline exactly (now == lease_deadline),
    both record_outbox_success and record_outbox_failure reject finalization without modifying state.
    """
    db_file = (tmp_path / "fencing_boundary.sqlite").as_posix()
    db_url = f"sqlite+pysqlite:///{db_file}"
    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    settings = Settings(
        GEMINI_API_KEY="test-key",
        DATABASE_URL=db_url,
        AUTH_SECRET_KEY=TEST_AUTH_SECRET.get_secret_value(),
        OTP_HASH_SECRET=TEST_OTP_SECRET.get_secret_value(),
        ENVIRONMENT="development",
    )

    with session_factory() as session:
        order = _create_test_order(session)
        ver, job = _seed_pending_outbox_job(session, order, settings, otp_code="888888")

        claims = claim_outbox_jobs(session, limit=1, lease_seconds=30)
        assert len(claims) == 1
        claim = claims[0]
        token = claim["lease_token"]
        deadline = claim["lease_deadline"]

        # Deterministically freeze utc_now to exactly equal the lease_deadline
        fixed_now = ensure_utc(deadline)
        monkeypatch.setattr("backend.app.services.guest_verification_service.utc_now", lambda: fixed_now)

        # 1. Attempt success finalization at exact boundary -> Must return False
        success = record_outbox_success(session, job.id, token)
        assert success is False

        # Verify job remains completely unmodified
        reloaded = session.scalar(select(GuestEmailOutbox).where(GuestEmailOutbox.id == job.id))
        session.refresh(reloaded)
        assert reloaded.status == OutboxDeliveryStatus.PENDING.value
        assert reloaded.sent_at is None
        assert reloaded.lease_token == token
        assert reloaded.encrypted_otp_payload is not None

        # 2. Attempt failure finalization at exact boundary -> Must return False
        failure = record_outbox_failure(
            session, job.id, ver.id, token, Exception("Failure at boundary")
        )
        assert failure is False

        # Verify job and verification remain completely unmodified
        session.refresh(reloaded)
        assert reloaded.status == OutboxDeliveryStatus.PENDING.value
        assert reloaded.attempts == 0
        assert reloaded.last_error is None
        assert reloaded.lease_token == token
        assert reloaded.encrypted_otp_payload is not None

        reloaded_ver = session.scalar(select(GuestOrderVerification).where(GuestOrderVerification.id == ver.id))
        session.refresh(reloaded_ver)
        assert reloaded_ver.consumed_at is None
        assert reloaded_ver.delivery_failed is False

    engine.dispose()
