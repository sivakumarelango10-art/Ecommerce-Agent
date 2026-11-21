from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from backend.app.api.auth_dependencies import get_current_user
from backend.app.db.models import User
from backend.app.db.session import get_db_session
from backend.app.schemas.order import (
    OrderItemResponse,
    OrderResponse,
    OrderSummaryResponse,
)
from backend.app.services.order_service import (
    format_order_response,
    format_order_summary_response,
    get_customer_order,
    list_customer_orders,
)

router = APIRouter(prefix="/orders", tags=["orders"])


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
