from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from backend.app.api.auth_dependencies import (
    get_current_user,
    get_verified_guest_order,
)
from backend.app.db.models import Order, User
from backend.app.db.session import get_db_session
from backend.app.schemas.guest_order import (
    GenericVerificationResponse,
    GuestOrderResponse,
    GuestTokenResponse,
    GuestVerificationRequest,
    GuestVerifyOTPRequest,
)
from backend.app.schemas.order import (
    OrderItemResponse,
    OrderResponse,
    OrderSummaryResponse,
)
from backend.app.services.email_service import (
    EmailConfigurationError,
    EmailDeliveryError,
)
from backend.app.services.guest_verification_service import (
    ExpiredOTPError,
    InvalidOTPError,
    MissingSecretError,
    OTPAttemptsExceededError,
    RateLimitExceededError,
    request_guest_verification,
    verify_guest_otp,
)
from backend.app.services.order_service import (
    format_guest_order_response,
    format_order_response,
    format_order_summary_response,
    get_customer_order,
    list_customer_orders,
)

router = APIRouter(prefix="/orders", tags=["orders"])


@router.post(
    "/guest/request-verification",
    response_model=GenericVerificationResponse,
    status_code=status.HTTP_200_OK,
    summary="Initiate guest order verification by sending a one-time passcode",
)
def request_guest_verification_endpoint(
    body: GuestVerificationRequest,
    request: Request,
    session: Session = Depends(get_db_session),
) -> GenericVerificationResponse:
    settings = request.app.state.settings
    try:
        request_guest_verification(session, body.order_number, body.email, settings)
    except RateLimitExceededError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(exc),
            headers={"Retry-After": str(exc.retry_after)},
        ) from exc
    except (EmailConfigurationError, EmailDeliveryError):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Email delivery service is currently unavailable.",
        ) from None
    except MissingSecretError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Verification service is currently unavailable.",
        ) from None
    return GenericVerificationResponse()


@router.post(
    "/guest/verify",
    response_model=GuestTokenResponse,
    status_code=status.HTTP_200_OK,
    summary="Verify guest OTP and receive a restricted guest access token",
)
def verify_guest_otp_endpoint(
    body: GuestVerifyOTPRequest,
    request: Request,
    session: Session = Depends(get_db_session),
) -> GuestTokenResponse:
    settings = request.app.state.settings
    try:
        order, token, expires_in = verify_guest_otp(
            session, body.order_number, body.email, body.otp, settings
        )
        return GuestTokenResponse(
            access_token=token,
            token_type="Bearer",
            expires_in=expires_in,
            order_number=order.order_number,
        )
    except (InvalidOTPError, ExpiredOTPError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except OTPAttemptsExceededError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc
    except MissingSecretError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Verification service is currently unavailable.",
        ) from None


@router.get(
    "/guest/{order_number}",
    response_model=GuestOrderResponse,
    status_code=status.HTTP_200_OK,
    summary="Retrieve full details of verified order using restricted guest token",
)
def get_guest_order_endpoint(
    order_number: str,
    order: Order = Depends(get_verified_guest_order),
) -> GuestOrderResponse:
    if order.order_number.upper() != order_number.strip().upper():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Guest access token is not authorized for this order number.",
        )
    return format_guest_order_response(order)



@router.get(
    "",
    response_model=list[OrderSummaryResponse],
    status_code=status.HTTP_200_OK,
    summary="List order history for authenticated customer",
)
def get_orders(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> list[OrderSummaryResponse]:
    """Retrieve paginated order history for the current authenticated user."""
    orders = list_customer_orders(session, user.id, limit=limit, offset=offset)
    return [format_order_summary_response(order) for order in orders]


@router.get(
    "/{order_id}",
    response_model=OrderResponse,
    status_code=status.HTTP_200_OK,
    summary="Get detailed order by ID",
)
def get_order_by_id(
    order_id: UUID,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> OrderResponse:
    """Retrieve full details of an individual order.

    Returns 404 if the order does not exist or does not belong to the authenticated user.
    """
    order = get_customer_order(session, user.id, order_id)
    if order is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Order not found.",
        )
    return format_order_response(order)


@router.get(
    "/{order_id}/items",
    response_model=list[OrderItemResponse],
    status_code=status.HTTP_200_OK,
    summary="List items for a specific order",
)
def get_order_items(
    order_id: UUID,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> list[OrderItemResponse]:
    """Retrieve items belonging to a specific customer order.

    Returns 404 if the order does not exist or does not belong to the authenticated user.
    """
    order = get_customer_order(session, user.id, order_id)
    if order is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Order not found.",
        )
    full_response = format_order_response(order)
    return full_response.items
