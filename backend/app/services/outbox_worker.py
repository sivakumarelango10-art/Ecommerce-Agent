"""Background worker for reliable transactional email outbox processing."""
import asyncio
import logging
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session, sessionmaker

from backend.app.core.config import Settings
from backend.app.services.email_service import EmailDeliveryProvider, get_email_provider
from backend.app.services.guest_verification_service import process_email_outbox

logger = logging.getLogger(__name__)


class GuestEmailOutboxWorker:
    """Reliable background worker for transactional email outbox processing.

    Integrates with FastAPI lifespan, sweeps pending and retryable jobs automatically,
    releases database connection locks during SMTP network calls via lease claiming,
    and supports graceful shutdown.
    """

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: Settings,
        email_provider: EmailDeliveryProvider | None = None,
        poll_interval_seconds: float = 2.0,
        batch_size: int = 10,
        lease_seconds: int = 60,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.email_provider = email_provider
        self.poll_interval_seconds = poll_interval_seconds
        self.batch_size = batch_size
        self.lease_seconds = lease_seconds
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._is_running = False

    @property
    def is_running(self) -> bool:
        """Return True if the background worker loop is currently running."""
        return self._is_running

    def process_batch(self) -> int:
        """Execute a single sweep of pending/retryable outbox jobs."""
        with self.session_factory() as session:
            provider = self.email_provider or get_email_provider(self.settings)
            return process_email_outbox(
                session=session,
                settings=self.settings,
                email_provider=provider,
                limit=self.batch_size,
                lease_seconds=self.lease_seconds,
            )

    async def _run_loop(self) -> None:
        """Internal asynchronous loop polling and processing outbox jobs."""
        logger.info(
            "Guest email outbox worker started",
            extra={
                "event": "outbox_worker_started",
                "poll_interval": self.poll_interval_seconds,
                "batch_size": self.batch_size,
            },
        )
        self._is_running = True
        try:
            while not self._stop_event.is_set():
                try:
                    # Run synchronous database + SMTP work in thread pool to avoid blocking the event loop
                    await asyncio.to_thread(self.process_batch)
                except Exception as exc:
                    logger.warning(
                        "Outbox worker sweep error: %s",
                        exc,
                        extra={"event": "outbox_worker_sweep_error", "error": str(exc)},
                    )

                # Wait for poll interval or until shutdown signal
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=self.poll_interval_seconds,
                    )
                except asyncio.TimeoutError:
                    pass
        finally:
            self._is_running = False
            logger.info(
                "Guest email outbox worker stopped",
                extra={"event": "outbox_worker_stopped"},
            )

    async def start(self) -> None:
        """Start the background worker task."""
        if self._task is not None and not self._task.done():
            return
        self._stop_event.clear()
        self._is_running = True
        self._task = asyncio.create_task(
            self._run_loop(), name="guest_email_outbox_worker"
        )
        # Yield control briefly so the background task starts execution
        await asyncio.sleep(0)

    async def stop(self, timeout: float = 10.0) -> None:
        """Gracefully stop the background worker task."""
        if self._task is None:
            self._is_running = False
            return
        self._stop_event.set()
        try:
            await asyncio.wait_for(asyncio.shield(self._task), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning(
                "Outbox worker shutdown timed out; cancelling task",
                extra={"event": "outbox_worker_shutdown_timeout"},
            )
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        except Exception:
            pass
        finally:
            self._task = None
            self._is_running = False
