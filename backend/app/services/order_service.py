from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from backend.app.db.models import Order
from backend.app.schemas.guest_order import (
    GuestOrderItemResponse,
    GuestOrderResponse,
)
from backend.app.schemas.order import (
    CustomerOrderDetailSummary,
    CustomerOrderItemSummary,
    CustomerOrderStatusSummary,
    OrderItemResponse,
    OrderResponse,
    OrderSummaryResponse,
    ShippingAddressSnapshotResponse,
)


def list_customer_orders(
    session: Session,
    user_id: UUID,
    limit: int = 20,
    offset: int = 0,
) -> list[Order]:
    """Retrieve paginated list of orders owned by the authenticated customer."""
    safe_limit = max(1, min(limit, 100))
    safe_offset = max(0, offset)
    stmt = (
        select(Order)
        .where(Order.user_id == user_id)
        .options(selectinload(Order.items))
        .order_by(Order.created_at.desc(), Order.id.desc())
        .limit(safe_limit)
        .offset(safe_offset)
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


def get_customer_order_by_number(
    session: Session,
    user_id: UUID,
    order_number: str,
) -> Order | None:
    """Retrieve an individual order by its public order number, strictly customer-scoped.

    Returns None if order does not exist, belongs to another customer, or if order_number is blank.
    """
    clean_num = order_number.strip() if isinstance(order_number, str) else ""
    if not clean_num:
        return None
    stmt = (
        select(Order)
        .where(
            Order.order_number == clean_num.upper(),
            Order.user_id == user_id,
        )
        .options(selectinload(Order.items))
    )
    return session.scalar(stmt)


def get_customer_order_status(
    session: Session,
    user_id: UUID,
    order_number: str,
) -> CustomerOrderStatusSummary | None:
    """Retrieve lightweight status summary for an order owned by customer."""
    order = get_customer_order_by_number(session, user_id, order_number)
    if order is None:
        return None
    return format_customer_order_status(order)


def get_customer_order_items(
    session: Session,
    user_id: UUID,
    order_number: str,
) -> list[CustomerOrderItemSummary] | None:
    """Retrieve line items for an order owned by customer."""
    order = get_customer_order_by_number(session, user_id, order_number)
    if order is None:
        return None
    return format_customer_order_items(order)


def format_customer_order_status(order: Order) -> CustomerOrderStatusSummary:
    """Format an Order ORM instance into the CustomerOrderStatusSummary schema."""
    return CustomerOrderStatusSummary(
        order_number=order.order_number,
        status=order.status,
        currency=order.currency,
        total_amount=order.total_amount,
        item_count=len(order.items),
        created_at=order.created_at,
        updated_at=order.updated_at,
    )


def format_customer_order_items(order: Order) -> list[CustomerOrderItemSummary]:
    """Format an Order ORM instance's line items into CustomerOrderItemSummary list."""
    return [
        CustomerOrderItemSummary(
            product_sku=item.product_sku,
            product_name=item.product_name,
            quantity=item.quantity,
            unit_price=item.unit_price,
            line_total=item.line_total,
        )
        for item in order.items
    ]


def format_customer_order_detail(order: Order) -> CustomerOrderDetailSummary:
    """Format an Order ORM instance into the CustomerOrderDetailSummary schema."""
    shipping_snapshot = ShippingAddressSnapshotResponse(
        recipient_name=order.shipping_recipient_name,
        line1=order.shipping_line1,
        line2=order.shipping_line2,
        city=order.shipping_city,
        region=order.shipping_region,
        postal_code=order.shipping_postal_code,
        country_code=order.shipping_country_code,
    )
    return CustomerOrderDetailSummary(
        order_number=order.order_number,
        status=order.status,
        currency=order.currency,
        subtotal=order.subtotal,
        shipping_fee=order.shipping_fee,
        tax=order.tax,
        total_amount=order.total_amount,
        created_at=order.created_at,
        shipping_snapshot=shipping_snapshot,
        items=format_customer_order_items(order),
    )


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


def format_guest_order_response(order: Order) -> GuestOrderResponse:
    """Format an Order ORM instance into the restricted GuestOrderResponse schema."""
    shipping_snapshot = ShippingAddressSnapshotResponse(
        recipient_name=order.shipping_recipient_name,
        line1=order.shipping_line1,
        line2=order.shipping_line2,
        city=order.shipping_city,
        region=order.shipping_region,
        postal_code=order.shipping_postal_code,
        country_code=order.shipping_country_code,
    )
    items = [
        GuestOrderItemResponse(
            product_sku=item.product_sku,
            product_name=item.product_name,
            quantity=item.quantity,
            unit_price=item.unit_price,
            line_total=item.line_total,
        )
        for item in order.items
    ]
    return GuestOrderResponse(
        order_number=order.order_number,
        created_at=order.created_at,
        status=order.status,
        currency=order.currency,
        subtotal=order.subtotal,
        shipping_fee=order.shipping_fee,
        tax=order.tax,
        total_amount=order.total_amount,
        shipping_snapshot=shipping_snapshot,
        items=items,
    )
