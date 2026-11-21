from email.message import EmailMessage
import logging
import smtplib
import socket
from typing import Protocol
from threading import Lock

from backend.app.core.config import Settings

logger = logging.getLogger(__name__)


class EmailServiceError(Exception):
    """Base error for email delivery issues."""


class EmailConfigurationError(EmailServiceError):
    """Raised when email delivery configuration is missing or invalid."""


class EmailDeliveryError(EmailServiceError):
    """Raised when email delivery to the SMTP server fails."""


class EmailDeliveryProvider(Protocol):
    """Protocol interface for email delivery providers."""

    def send_otp_email(
        self, recipient_email: str, order_number: str, otp_code: str
    ) -> None:
        """Deliver a one-time verification code to the recipient."""
        ...


class InMemoryEmailDeliveryProvider:
    """In-memory email delivery provider capturing messages for test verification.

    Strictly prohibited in production environments.
    """

    def __init__(self) -> None:
        self._sent_messages: list[dict[str, str]] = []
        self._lock = Lock()

    def send_otp_email(
        self, recipient_email: str, order_number: str, otp_code: str
    ) -> None:
        with self._lock:
            self._sent_messages.append(
                {
                    "recipient": recipient_email,
                    "order_number": order_number,
                    "otp_code": otp_code,
                }
            )
        logger.info(
            "Captured OTP email in memory",
            extra={"event": "in_memory_email_sent", "order_number": order_number},
        )

    def get_sent_messages(self) -> list[dict[str, str]]:
        with self._lock:
            return list(self._sent_messages)

    def clear(self) -> None:
        with self._lock:
            self._sent_messages.clear()


class SMTPEmailDeliveryProvider:
    """Production-grade SMTP email delivery provider with TLS/SSL support."""

    def __init__(self, settings: Settings) -> None:
        if not settings.smtp_host or not settings.smtp_from_email:
            raise EmailConfigurationError(
                "SMTP configuration requires SMTP_HOST and SMTP_FROM_EMAIL."
            )
        self._host = settings.smtp_host
        self._port = settings.smtp_port
        self._username = settings.smtp_username
        self._password = settings.smtp_password
        self._from_email = settings.smtp_from_email
        self._use_tls = settings.smtp_use_tls
        self._use_ssl = settings.smtp_use_ssl

    def send_otp_email(
        self, recipient_email: str, order_number: str, otp_code: str
    ) -> None:
        msg = EmailMessage()
        msg["Subject"] = f"Your Verification Code for Order {order_number}"
        msg["From"] = self._from_email
        msg["To"] = recipient_email
        msg.set_content(
            f"Hello,\n\n"
            f"Your single-use verification code for order {order_number} is:\n\n"
            f"    {otp_code}\n\n"
            f"This code will expire in 5 minutes. If you did not request this code, please ignore this email.\n"
        )

        try:
            if self._use_ssl:
                server = smtplib.SMTP_SSL(self._host, self._port, timeout=10)
            else:
                server = smtplib.SMTP(self._host, self._port, timeout=10)

            with server:
                if self._use_tls and not self._use_ssl:
                    server.starttls()
                if self._username and self._password:
                    server.login(
                        self._username.get_secret_value(),
                        self._password.get_secret_value(),
                    )
                server.send_message(msg)

            logger.info(
                "Successfully dispatched verification email via SMTP",
                extra={"event": "smtp_email_sent", "order_number": order_number},
            )
        except (smtplib.SMTPException, socket.error, TimeoutError) as exc:
            logger.error(
                "SMTP email delivery failed",
                extra={
                    "event": "smtp_email_failure",
                    "order_number": order_number,
                    "error_type": type(exc).__name__,
                },
            )
            raise EmailDeliveryError(
                "Failed to deliver verification code via email."
            ) from exc


_active_email_provider: EmailDeliveryProvider | None = None
_default_dev_provider = InMemoryEmailDeliveryProvider()


def set_email_provider(provider: EmailDeliveryProvider | None) -> None:
    """Set or override the active email delivery provider (for test suites)."""
    global _active_email_provider
    _active_email_provider = provider


def get_email_provider(settings: Settings) -> EmailDeliveryProvider:
    """Resolve the appropriate email delivery provider based on configuration and environment."""
    global _active_email_provider
    is_production = settings.environment.lower() == "production"

    if _active_email_provider is not None:
        if is_production and isinstance(
            _active_email_provider, InMemoryEmailDeliveryProvider
        ):
            raise EmailConfigurationError(
                "In-memory test email provider is prohibited in production environment."
            )
        return _active_email_provider

    if is_production:
        if not settings.smtp_host or not settings.smtp_from_email:
            raise EmailConfigurationError(
                "Production email delivery is not configured. SMTP settings are required."
            )
        return SMTPEmailDeliveryProvider(settings)

    # In development/test environments
    if settings.smtp_host and settings.smtp_from_email:
        return SMTPEmailDeliveryProvider(settings)

    return _default_dev_provider
