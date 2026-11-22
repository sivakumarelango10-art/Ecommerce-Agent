from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from backend.app.api.dependencies import get_settings
from backend.app.core.config import Settings
from backend.app.db.session import get_db_session
from backend.app.schemas.auth import (
    LoginRequest,
    RegisterRequest,
    TokenResponse,
    UserResponse,
)
from backend.app.security.rate_limiter import check_and_record_auth_rate_limit
from backend.app.services.auth_service import (
    EmailAlreadyRegisteredError,
    InvalidCredentialsError,
    login_user,
    register_user,
)

router = APIRouter(prefix="/auth", tags=["authentication"])


@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
)
def register(
    body: RegisterRequest,
    request: Request,
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> UserResponse:
    check_and_record_auth_rate_limit(
        session=session,
        request=request,
        action="register",
        identifier=body.email,
        settings=settings,
    )
    try:
        user = register_user(session, body)
    except EmailAlreadyRegisteredError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists.",
        ) from None
    return UserResponse.model_validate(user)


@router.post("/login", response_model=TokenResponse)
def login(
    body: LoginRequest,
    request: Request,
    settings: Settings = Depends(get_settings),
    session: Session = Depends(get_db_session),
) -> TokenResponse:
    check_and_record_auth_rate_limit(
        session=session,
        request=request,
        action="login",
        identifier=body.email,
        settings=settings,
    )
    try:
        token = login_user(session, body, settings)
    except InvalidCredentialsError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email or password is incorrect.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None
    return TokenResponse(
        access_token=token.access_token,
        expires_in=token.expires_in,
    )
