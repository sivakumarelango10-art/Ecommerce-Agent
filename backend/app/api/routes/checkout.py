import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from sqlalchemy.orm import Session

from backend.app.api.auth_dependencies import get_current_user
from backend.app.api.dependencies import get_settings
from backend.app.core.config import Settings
from backend.app.db.models import User
from backend.app.db.session import get_db_session
from backend.app.schemas.order import CheckoutRequest, OrderResponse
from backend.app.services.checkout_service import process_checkout
from backend.app.services.order_service import format_order_response
from backend.app.services.shipping_tax_policy import PolicyConfigurationError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/checkout", tags=["checkout"])


@router.post(
    "",
    response_model=OrderResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Checkout customer's cart into a confirmed order",
)
def post_checkout(
    body: CheckoutRequest,
    response: Response,
    idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        min_length=1,
        max_length=128,
        description="Mandatory client-generated idempotency key",
    ),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> OrderResponse:
    """Execute complete checkout workflow.

    Transaction ownership:
    - The route handler owns commit and rollback boundaries.
    - If successful, commits all mutations atomically (order, items, stock, cart clear).
    - If failure occurs (price change, insufficient stock, invalid address),
      explicitly rolls back and raises the appropriate HTTP error.
    """
    try:
        order, is_replay = process_checkout(
            session=session,
            user_id=user.id,
            request=body,
            idempotency_key=idempotency_key,
            settings=settings,
        )
        session.commit()
    except PolicyConfigurationError as exc:
        session.rollback()
        logger.error(
            "Store policy configuration error during checkout",
            extra={"event": "checkout_policy_error", "error_type": type(exc).__name__},
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Checkout service is temporarily unavailable due to unconfigured store policies.",
        ) from exc
    except Exception:
        session.rollback()
        raise

    if is_replay:
        response.status_code = status.HTTP_200_OK
        response.headers["Idempotent-Replay"] = "true"

    return format_order_response(order)
