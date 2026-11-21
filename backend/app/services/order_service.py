from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from backend.app.db.models import Order
from backend.app.schemas.order import (
    OrderItemResponse,
    OrderResponse,
    OrderSummaryResponse,
    ShippingAddressSnapshotResponse,
)


def list_customer_orders(
    session: Session,
    user_id: UUID,
    *,
    limit: int = 20,
    offset: int = 0,
) -> list[Order]:
    """Retrieve paginated list of orders owned by the authenticated customer."""
    stmt = (
        select(Order)
        .where(Order.user_id == user_id)
        .options(selectinload(Order.items))
        .order_by(Order.created_at.desc(), Order.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(session.scalars(stmt).all())


def get_customer_order(
    session: Session,
    user_id: UUID,
    order_id: UUID,
) -> Order | None:
    """Retrieve full details of an individual order owned by customer.

    Returns None if order does not exist or belongs to another customer.
    """
    stmt = (
        select(Order)
        .where(Order.id == order_id, Order.user_id == user_id)
        .options(selectinload(Order.items))
    )
    return session.scalar(stmt)


def format_order_response(order: Order) -> OrderResponse:
    """Format an Order ORM instance into the full OrderResponse schema."""
    items = [
        OrderItemResponse(
            id=item.id,
            product_id=item.product_id,
            product_sku=item.product_sku,
            product_name=item.product_name,
            unit_price=item.unit_price,
            quantity=item.quantity,
            line_total=item.line_total,
            created_at=item.created_at,
        )
        for item in order.items
    ]

    shipping_snapshot = ShippingAddressSnapshotResponse(
        recipient_name=order.shipping_recipient_name,
        line1=order.shipping_line1,
        line2=order.shipping_line2,
        city=order.shipping_city,
        region=order.shipping_region,
        postal_code=order.shipping_postal_code,
        country_code=order.shipping_country_code,
    )

    return OrderResponse(
        id=order.id,
        order_number=order.order_number,
        user_id=order.user_id,
        status=order.status,
        currency=order.currency,
        subtotal=order.subtotal,
        shipping_fee=order.shipping_fee,
        tax=order.tax,
        total_amount=order.total_amount,
        shipping_address_id=order.shipping_address_id,
        shipping_snapshot=shipping_snapshot,
        items=items,
        idempotency_key=order.idempotency_key,
        request_fingerprint=order.request_fingerprint,
        created_at=order.created_at,
        updated_at=order.updated_at,
    )


def format_order_summary_response(order: Order) -> OrderSummaryResponse:
    """Format an Order ORM instance into the lightweight OrderSummaryResponse schema."""
    return OrderSummaryResponse(
        id=order.id,
        order_number=order.order_number,
        user_id=order.user_id,
        status=order.status,
        currency=order.currency,
        total_amount=order.total_amount,
        item_count=len(order.items),
        created_at=order.created_at,
    )
