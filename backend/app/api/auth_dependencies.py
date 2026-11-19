from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from backend.app.db.models import User
from backend.app.db.session import get_db_session
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
