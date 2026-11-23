from datetime import datetime, timezone
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from backend.app.core.datetime_utils import ensure_utc, utc_now
from backend.app.db.models import GuestAccessSession, Order, User
from backend.app.db.session import get_db_session
from backend.app.security.guest_tokens import (
    compute_jti_hash,
    decode_guest_access_token,
)
from backend.app.security.tokens import decode_access_token

bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    session: Session = Depends(get_db_session),
) -> User:
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication is required.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise unauthorized

    try:
        user_id: UUID = decode_access_token(
            credentials.credentials,
            request.app.state.settings,
        )
    except (jwt.PyJWTError, ValueError):
        raise unauthorized from None

    user = session.get(User, user_id)
    if user is None or not user.is_active:
        raise unauthorized
    return user


def get_current_admin(
    request: Request,
    current_user: User = Depends(get_current_user),
) -> User:
    """Validate that the authenticated active user is on the admin allowlist."""
    settings = getattr(request.app.state, "settings", None)
    admin_emails: set[str] = getattr(settings, "admin_emails", None) or set()

    user_email = (current_user.email or "").strip().lower()
    if not admin_emails or user_email not in admin_emails:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges are required to access this resource.",
        )
    return current_user


def get_optional_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> User | None:
    """Return authenticated User if Bearer token is provided, or None for guests.

    Raises 401 if a token is provided but is invalid or expired.
    """
    if credentials is None:
        return None

    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication is required.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials.scheme.lower() != "bearer":
        raise unauthorized

    try:
        user_id: UUID = decode_access_token(
            credentials.credentials,
            request.app.state.settings,
        )
    except (jwt.PyJWTError, ValueError):
        raise unauthorized from None

    factory = getattr(request.app.state, "db_session_factory", None)
    if factory is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database service is unavailable.",
        )
    with factory() as session:
        user = session.get(User, user_id)
        if user is None or not user.is_active:
            raise unauthorized
        return user


def get_verified_guest_order(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    session: Session = Depends(get_db_session),
) -> Order:
    """Validate a restricted guest JWT and resolve the verified Order.

    Strictly rejects access tokens, expired tokens, revoked sessions, or missing orders.
    """
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Valid guest order access token is required.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise unauthorized

    try:
        payload = decode_guest_access_token(
            credentials.credentials,
            request.app.state.settings,
        )
    except (jwt.PyJWTError, ValueError):
        raise unauthorized from None

    order_id_str = payload.get("order_id")
    jti = payload.get("jti")
    if not order_id_str or not jti:
        raise unauthorized

    try:
        order_id = UUID(order_id_str)
    except ValueError:
        raise unauthorized

    jti_hash = compute_jti_hash(jti)
    now = utc_now()

    guest_session = session.scalar(
        select(GuestAccessSession).where(
            GuestAccessSession.token_jti_hash == jti_hash,
            GuestAccessSession.order_id == order_id,
        )
    )
    if guest_session is None:
        raise unauthorized
    if guest_session.revoked_at is not None:
        raise unauthorized
    session_exp = ensure_utc(guest_session.expires_at)
    if session_exp is None or session_exp <= now:
        raise unauthorized

    stmt = select(Order).where(Order.id == order_id).options(selectinload(Order.items))
    order = session.scalar(stmt)
    if order is None:
        raise unauthorized

    return order


def revoke_guest_session(session: Session, jti: str) -> bool:
    """Revoke a guest access session by token JTI."""
    jti_hash = compute_jti_hash(jti)
    guest_session = session.scalar(
        select(GuestAccessSession).where(
            GuestAccessSession.token_jti_hash == jti_hash
        )
    )
    if guest_session and guest_session.revoked_at is None:
        guest_session.revoked_at = utc_now()
        session.commit()
        return True
    return False
