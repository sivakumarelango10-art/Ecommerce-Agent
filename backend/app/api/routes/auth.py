from fastapi import APIRouter, Depends, HTTPException, status
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
    session: Session = Depends(get_db_session),
) -> UserResponse:
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
    settings: Settings = Depends(get_settings),
    session: Session = Depends(get_db_session),
) -> TokenResponse:
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
