from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.core.config import Settings
from backend.app.db.models import CustomerProfile, User
from backend.app.schemas.auth import LoginRequest, RegisterRequest
from backend.app.security.passwords import hash_password, verify_password
from backend.app.security.tokens import create_access_token


class EmailAlreadyRegisteredError(Exception):
    pass


class InvalidCredentialsError(Exception):
    pass


def register_user(session: Session, request: RegisterRequest) -> User:
    email = str(request.email).strip().lower()
    user = User(
        email=email,
        password_hash=hash_password(request.password),
        role="customer",
    )
    user.profile = CustomerProfile(display_name=request.display_name)
    session.add(user)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise EmailAlreadyRegisteredError from exc
    session.refresh(user)
    return user


@dataclass(frozen=True)
class AuthenticatedToken:
    access_token: str
    expires_in: int


def login_user(
    session: Session,
    request: LoginRequest,
    settings: Settings,
) -> AuthenticatedToken:
    email = str(request.email).strip().lower()
    user = session.scalar(select(User).where(User.email == email))
    if user is None:
        hash_password(request.password)
        raise InvalidCredentialsError
    password_matches = verify_password(request.password, user.password_hash)
    if not password_matches or not user.is_active:
        raise InvalidCredentialsError
    return AuthenticatedToken(
        access_token=create_access_token(user.id, settings),
        expires_in=settings.access_token_expire_minutes * 60,
    )
