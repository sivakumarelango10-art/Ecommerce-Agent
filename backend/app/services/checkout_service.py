from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import logging
import secrets
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from backend.app.core.config import Settings
from backend.app.db.models import (
    Address,
    Cart,
    CartItem,
    Inventory,
    Order,
    OrderItem,
    OrderStatus,
    Product,
)
from backend.app.schemas.order import (
    CheckoutItemAcknowledgement,
    CheckoutRequest,
    PriceChangeDetail,
)
from backend.app.services.cart_service import get_or_create_cart_for_update
from backend.app.services.shipping_tax_policy import (
    ShippingPolicyProtocol,
    TaxPolicyProtocol,
    get_shipping_policy,
    get_tax_policy,
    validate_policy_amount,
)

logger = logging.getLogger(__name__)

MAX_ORDER_NUMBER_RETRIES = 3


def compute_request_fingerprint(
    shipping_address_id: UUID,
    items: list[CheckoutItemAcknowledgement],
) -> str:
    """Compute a deterministic 64-character SHA-256 fingerprint from checkout parameters.

    Components included:
    - shipping_address_id
    - product_id (sorted in ascending lowercase hex order)
    - quantity for each product
    - acknowledged_unit_price for each product (quantized to 2 decimal places)
    """
    sorted_items = sorted(items, key=lambda x: str(x.product_id).lower())
    canonical_segments = [f"addr:{str(shipping_address_id).lower()}"]
    for item in sorted_items:
        price_str = str(
            item.acknowledged_unit_price.quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
        )
        canonical_segments.append(
            f"item:{str(item.product_id).lower()}:{item.quantity}:{price_str}"
        )
    canonical_payload = "|".join(canonical_segments)
    return hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest()


def generate_order_number() -> str:
    """Generate a collision-resistant unique order number."""
    date_part = datetime.now(timezone.utc).strftime("%Y%m%d")
    random_part = secrets.token_hex(4).upper()
    return f"ORD-{date_part}-{random_part}"


def is_order_number_collision(exc: IntegrityError) -> bool:
    """Determine whether an IntegrityError was caused specifically by ix_orders_order_number."""
    orig = getattr(exc, "orig", None)
    if hasattr(orig, "diag") and getattr(orig.diag, "constraint_name", None):
        return orig.diag.constraint_name == "ix_orders_order_number"
    err_str = str(exc).lower()
    return "ix_orders_order_number" in err_str or "orders.order_number" in err_str


def process_checkout(
    session: Session,
    user_id: UUID,
    request: CheckoutRequest,
    idempotency_key: str,
    settings: Settings,
    shipping_policy: ShippingPolicyProtocol | None = None,
    tax_policy: TaxPolicyProtocol | None = None,
) -> tuple[Order, bool]:
    """Execute complete checkout workflow inside a caller-owned transaction.

    Returns:
        tuple[Order, bool]: (order, is_replay)
    """
    # 1. Compute canonical SHA-256 fingerprint before acquiring locks
    fingerprint = compute_request_fingerprint(
        request.shipping_address_id, request.items
    )

    # 2. Idempotency Pre-check (autobegins transaction on read)
    existing_order = session.scalar(
        select(Order)
        .where(
            Order.user_id == user_id,
            Order.idempotency_key == idempotency_key,
        )
        .options(selectinload(Order.items))
    )
    if existing_order is not None:
        if existing_order.request_fingerprint == fingerprint:
            return existing_order, True
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Idempotency key reused with different request parameters.",
        )

    # 3. Lock parent Cart row (with_for_update)
    cart = get_or_create_cart_for_update(session, user_id)

    # 4. Post-Lock Idempotency Recheck
    existing_order = session.scalar(
        select(Order)
        .where(
            Order.user_id == user_id,
            Order.idempotency_key == idempotency_key,
        )
        .options(selectinload(Order.items))
    )
    if existing_order is not None:
        if existing_order.request_fingerprint == fingerprint:
            return existing_order, True
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Idempotency key reused with different request parameters.",
        )

    # 5. Validate Cart is not empty
    if not cart.items:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Shopping cart is empty.",
        )

    # 6. Verify Acknowledged Cart Snapshot against actual database cart
    if len(request.items) != len(cart.items):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cart snapshot does not match current cart contents.",
        )
    cart_items_map = {item.product_id: item.quantity for item in cart.items}
    for req_item in request.items:
        if (
            req_item.product_id not in cart_items_map
            or cart_items_map[req_item.product_id] != req_item.quantity
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Cart snapshot does not match current cart contents.",
            )

    # 7. Lock and validate shipping address
    address = session.scalar(
        select(Address)
        .where(
            Address.id == request.shipping_address_id,
            Address.user_id == user_id,
        )
        .with_for_update()
    )
    if address is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Shipping address not found.",
        )

    # 8. Lock Products and Inventory deterministically (ORDER BY id / product_id ASC)
    sorted_pids = sorted(list({item.product_id for item in cart.items}))
    products = list(
        session.scalars(
            select(Product)
            .where(Product.id.in_(sorted_pids))
            .order_by(Product.id.asc())
            .with_for_update()
        ).all()
    )
    product_map = {p.id: p for p in products}

    inventories = list(
        session.scalars(
            select(Inventory)
            .where(Inventory.product_id.in_(sorted_pids))
            .order_by(Inventory.product_id.asc())
            .with_for_update()
        ).all()
    )
    inventory_map = {inv.product_id: inv for inv in inventories}

    # 9. Revalidate product active status and available inventory
    for item in cart.items:
        prod = product_map.get(item.product_id)
        if prod is None or not prod.is_active:
            name = prod.name if prod else "Unknown"
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Product '{name}' is no longer available.",
            )
        inv = inventory_map.get(item.product_id)
        available = inv.available_quantity if inv else 0
        if available < item.quantity:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Insufficient stock for '{prod.name}'. Available: {available}, requested: {item.quantity}.",
            )

    # 10. Validate Price Acknowledgement
    req_item_map = {item.product_id: item for item in request.items}
    price_changes: list[PriceChangeDetail] = []
    for item in cart.items:
        prod = product_map[item.product_id]
        current_price = Decimal(str(prod.price)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        req_item = req_item_map[item.product_id]
        ack_price = Decimal(str(req_item.acknowledged_unit_price)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        if current_price != ack_price:
            price_changes.append(
                PriceChangeDetail(
                    product_id=prod.id,
                    product_sku=prod.sku,
                    product_name=prod.name,
                    acknowledged_price=ack_price,
                    current_price=current_price,
                    currency=settings.catalogue_currency,
                )
            )

    if price_changes:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "PRICE_CHANGED",
                "detail": "One or more product prices have changed. Please review and acknowledge updated prices before checking out.",
                "price_changes": [p.model_dump(mode="json") for p in price_changes],
            },
        )

    # 11. Calculate Subtotal, Shipping Fee, and Tax
    subtotal = Decimal("0.00")
    for item in cart.items:
        prod = product_map[item.product_id]
        unit_price = Decimal(str(prod.price)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        line_total = (unit_price * Decimal(item.quantity)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        subtotal += line_total
    subtotal = subtotal.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    active_shipping_policy = shipping_policy or get_shipping_policy(settings)
    raw_shipping_fee = active_shipping_policy.calculate_shipping_fee(
        subtotal=subtotal,
        items=cart.items,
        shipping_address=address,
        settings=settings,
    )
    shipping_fee = validate_policy_amount(raw_shipping_fee, "Shipping policy")

    active_tax_policy = tax_policy or get_tax_policy(settings)
    raw_tax = active_tax_policy.calculate_tax(
        subtotal=subtotal,
        shipping_fee=shipping_fee,
        items=cart.items,
        shipping_address=address,
        settings=settings,
    )
    tax = validate_policy_amount(raw_tax, "Tax policy")

    total_amount = (subtotal + shipping_fee + tax).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )

    # 12. Create Order record with collision retry for ix_orders_order_number
    order: Order | None = None
    for attempt in range(1, MAX_ORDER_NUMBER_RETRIES + 1):
        candidate_order_number = generate_order_number()
        candidate_order = Order(
            order_number=candidate_order_number,
            user_id=user_id,
            shipping_address_id=address.id,
            shipping_recipient_name=address.recipient_name,
            shipping_line1=address.line1,
            shipping_line2=address.line2,
            shipping_city=address.city,
            shipping_region=address.region,
            shipping_postal_code=address.postal_code,
            shipping_country_code=address.country_code,
            status=OrderStatus.CONFIRMED.value,
            currency=settings.catalogue_currency,
            subtotal=subtotal,
            shipping_fee=shipping_fee,
            tax=tax,
            total_amount=total_amount,
            idempotency_key=idempotency_key,
            request_fingerprint=fingerprint,
        )
        try:
            with session.begin_nested():
                session.add(candidate_order)
                session.flush()
                order = candidate_order
                break
        except IntegrityError as exc:
            if not is_order_number_collision(exc):
                raise
            if candidate_order in session:
                session.expunge(candidate_order)
            if attempt == MAX_ORDER_NUMBER_RETRIES:
                logger.error(
                    "Exhausted unique order number generation retries",
                    extra={"event": "order_number_collision_exhausted", "attempts": attempt},
                )
                raise RuntimeError(
                    "Failed to allocate a unique order number after retries."
                ) from exc

    assert order is not None

    # 13. Create OrderItem snapshots
    for item in cart.items:
        prod = product_map[item.product_id]
        unit_price = Decimal(str(prod.price)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        line_total = (unit_price * Decimal(item.quantity)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        order_item = OrderItem(
            order_id=order.id,
            product_id=prod.id,
            product_sku=prod.sku,
            product_name=prod.name,
            unit_price=unit_price,
            quantity=item.quantity,
            line_total=line_total,
        )
        session.add(order_item)

    # 14. Deduct inventory atomically
    for item in cart.items:
        inventory_map[item.product_id].available_quantity -= item.quantity

    session.flush()

    # 15. Clear customer's cart items
    session.execute(delete(CartItem).where(CartItem.cart_id == cart.id))
    session.flush()

    return order, False
