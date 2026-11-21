from decimal import Decimal, ROUND_HALF_UP
import logging
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from backend.app.core.constants import DEFAULT_CATALOGUE_CURRENCY
from backend.app.db.models import Cart, CartItem, Product
from backend.app.schemas.cart import CartItemResponse, CartResponse

logger = logging.getLogger(__name__)


def is_cart_user_collision(exc: IntegrityError) -> bool:
    """Determine whether an IntegrityError was caused specifically by uq_carts_user_id."""
    orig = getattr(exc, "orig", None)
    if hasattr(orig, "diag") and getattr(orig.diag, "constraint_name", None):
        return orig.diag.constraint_name == "uq_carts_user_id"
    err_str = str(exc).lower()
    return "uq_carts_user_id" in err_str or "carts.user_id" in err_str


def get_or_create_cart_for_update(
    session: Session,
    user_id: UUID,
) -> Cart:
    """Retrieve the cart for the user under an exclusive row lock or create one safely.

    Guarantees:
    - Serializes with any other transaction mutating the user's cart or checking out.
    - Uses a database SAVEPOINT (begin_nested) to isolate cart creation.
    - If a concurrent transaction created the cart, recovers from IntegrityError on
      uq_carts_user_id, rolls back savepoint, and locks the winning cart row with with_for_update().
    - Returns an exclusively locked Cart row in all scenarios.
    """
    cart = session.scalar(
        select(Cart)
        .where(Cart.user_id == user_id)
        .options(selectinload(Cart.items).selectinload(CartItem.product))
        .with_for_update()
    )
    if cart is not None:
        return cart

    try:
        with session.begin_nested():
            cart = Cart(user_id=user_id)
            session.add(cart)
            session.flush()
    except IntegrityError as exc:
        if not is_cart_user_collision(exc):
            raise
        # Savepoint rolled back automatically. Re-select the winning cart row with FOR UPDATE.
        cart = session.scalar(
            select(Cart)
            .where(Cart.user_id == user_id)
            .options(selectinload(Cart.items).selectinload(CartItem.product))
            .with_for_update()
        )
        if cart is None:
            raise exc
    else:
        session.expire(cart, ["items"])
        cart = (
            session.scalar(
                select(Cart)
                .where(Cart.id == cart.id)
                .options(selectinload(Cart.items).selectinload(CartItem.product))
                .with_for_update()
            )
            or cart
        )

    return cart


def get_or_create_cart(
    session: Session,
    user_id: UUID,
    *,
    auto_commit: bool = False,
) -> Cart:
    """Retrieve the cart for the user or create one safely if it does not exist.

    Transaction ownership policy:
    - Uses a database SAVEPOINT (begin_nested) to isolate cart creation.
    - If a concurrent transaction already created the cart, the savepoint
      rolls back to the savepoint without aborting the caller's outer transaction.
    - Does NOT commit the caller's session unless auto_commit=True is explicitly requested.
    """
    cart = session.scalar(
        select(Cart)
        .where(Cart.user_id == user_id)
        .options(selectinload(Cart.items).selectinload(CartItem.product))
    )
    if cart is not None:
        return cart

    try:
        with session.begin_nested():
            cart = Cart(user_id=user_id)
            session.add(cart)
            session.flush()
    except IntegrityError as exc:
        if not is_cart_user_collision(exc):
            raise
        # The begin_nested context manager rolled back to the savepoint.
        # The caller's outer transaction remains intact and healthy.
        cart = session.scalar(
            select(Cart)
            .where(Cart.user_id == user_id)
            .options(selectinload(Cart.items).selectinload(CartItem.product))
        )
        if cart is None:
            raise exc
    else:
        session.expire(cart, ["items"])
        cart = (
            session.scalar(
                select(Cart)
                .where(Cart.id == cart.id)
                .options(selectinload(Cart.items).selectinload(CartItem.product))
            )
            or cart
        )

    if auto_commit:
        session.commit()
        session.expire(cart, ["items"])

    return cart


def get_cart(
    session: Session,
    user_id: UUID,
    *,
    auto_commit: bool = False,
) -> Cart:
    """Retrieve the authenticated user's cart (creating one if absent)."""
    cart = get_or_create_cart(session, user_id, auto_commit=auto_commit)
    session.expire(cart, ["items"])
    refreshed = session.scalar(
        select(Cart)
        .where(Cart.id == cart.id)
        .options(selectinload(Cart.items).selectinload(CartItem.product))
    )
    return refreshed or cart


def add_to_cart(
    session: Session,
    user_id: UUID,
    product_id: UUID,
    quantity: int = 1,
    *,
    auto_commit: bool = False,
) -> Cart:
    """Add a product to the cart or increase its quantity if already present.

    Locking & Concurrency Strategy:
    - Acquires parent Cart lock (with_for_update) FIRST to serialize with concurrent checkouts
      or other mutations for this user.
    - Preliminary check ensures product exists, is active, and has available inventory.
    - Existing items are locked with row-level locking (with_for_update) to serialize
      concurrent additions and prevent lost updates.
    - If the item is not yet present, a SAVEPOINT (begin_nested) handles concurrent insert
      races on uq_cart_items_cart_product without rolling back outer transaction work.
    - When an insert conflict occurs, the savepoint rolls back to savepoint, locks the
      newly inserted item with with_for_update, validates the combined quantity against
      available inventory, and increments atomically.
    - Does NOT commit the caller's session unless auto_commit=True is explicitly requested.
    """
    if quantity <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Quantity must be greater than zero.",
        )

    # 1. Acquire parent Cart lock first
    cart = get_or_create_cart_for_update(session, user_id)

    # 2. Inspect target product
    product = session.get(
        Product,
        product_id,
        options=[selectinload(Product.inventory)],
    )
    if product is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product not found.",
        )

    if not product.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Product is not available.",
        )

    available_qty = (
        product.inventory.available_quantity if product.inventory else 0
    )
    if available_qty <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Product is out of stock.",
        )

    # 3. Lock existing cart item with with_for_update to serialize concurrent updates
    existing_item = session.scalar(
        select(CartItem)
        .where(
            CartItem.cart_id == cart.id,
            CartItem.product_id == product.id,
        )
        .with_for_update()
    )

    if existing_item is not None:
        new_quantity = existing_item.quantity + quantity
        if new_quantity > available_qty:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Requested quantity ({new_quantity}) exceeds available inventory ({available_qty}).",
            )
        existing_item.quantity = new_quantity
        session.flush()
    else:
        if quantity > available_qty:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Requested quantity ({quantity}) exceeds available inventory ({available_qty}).",
            )
        try:
            with session.begin_nested():
                new_item = CartItem(
                    cart=cart,
                    product=product,
                    quantity=quantity,
                )
                session.add(new_item)
                session.flush()
        except IntegrityError:
            # Handle race condition: item was concurrently inserted by another transaction.
            # The begin_nested context manager rolled back the savepoint.
            # The outer transaction is completely intact.
            existing_item = session.scalar(
                select(CartItem)
                .where(
                    CartItem.cart_id == cart.id,
                    CartItem.product_id == product.id,
                )
                .with_for_update()
            )
            if existing_item is not None:
                new_quantity = existing_item.quantity + quantity
                if new_quantity > available_qty:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Requested quantity ({new_quantity}) exceeds available inventory ({available_qty}).",
                    )
                existing_item.quantity = new_quantity
                session.flush()
            else:
                raise

    if auto_commit:
        session.commit()

    session.expire(cart, ["items"])

    refreshed_cart = session.scalar(
        select(Cart)
        .where(Cart.id == cart.id)
        .options(selectinload(Cart.items).selectinload(CartItem.product))
    )
    return refreshed_cart or cart


def update_cart_item_quantity(
    session: Session,
    user_id: UUID,
    item_id: UUID,
    quantity: int,
    *,
    auto_commit: bool = False,
) -> Cart:
    """Update the quantity of an existing cart item owned by user.

    Validates:
    - Parent Cart row is locked first (with_for_update).
    - Quantity must be positive integer.
    - Item must belong to the user's cart.
    - Uses with_for_update row-level locking on CartItem.
    - Product must still be active.
    - Quantity cannot exceed available inventory.
    """
    if quantity <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Quantity must be greater than zero.",
        )

    # 1. Lock parent cart row
    cart = get_or_create_cart_for_update(session, user_id)

    # 2. Lock item
    item = session.scalar(
        select(CartItem)
        .where(CartItem.id == item_id, CartItem.cart_id == cart.id)
        .options(
            selectinload(CartItem.product).selectinload(Product.inventory)
        )
        .with_for_update()
    )
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Cart item not found.",
        )

    product = item.product
    if product is None or not product.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Product is not available.",
        )

    available_qty = (
        product.inventory.available_quantity if product.inventory else 0
    )
    if quantity > available_qty:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Requested quantity ({quantity}) exceeds available inventory ({available_qty}).",
        )

    item.quantity = quantity
    session.flush()

    if auto_commit:
        session.commit()

    session.expire(cart, ["items"])

    refreshed_cart = session.scalar(
        select(Cart)
        .where(Cart.id == cart.id)
        .options(selectinload(Cart.items).selectinload(CartItem.product))
    )
    return refreshed_cart or cart


def remove_cart_item(
    session: Session,
    user_id: UUID,
    item_id: UUID,
    *,
    auto_commit: bool = False,
) -> None:
    """Remove a single cart item from the authenticated user's cart."""
    # 1. Lock parent cart row
    cart = get_or_create_cart_for_update(session, user_id)

    item = session.scalar(
        select(CartItem).where(
            CartItem.id == item_id,
            CartItem.cart_id == cart.id,
        )
        .with_for_update()
    )
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Cart item not found.",
        )

    session.delete(item)
    session.flush()

    if auto_commit:
        session.commit()

    session.expire(cart, ["items"])


def clear_cart(
    session: Session,
    user_id: UUID,
    *,
    auto_commit: bool = False,
) -> Cart:
    """Remove all items from the authenticated user's cart."""
    # 1. Lock parent cart row
    cart = get_or_create_cart_for_update(session, user_id)
    session.execute(delete(CartItem).where(CartItem.cart_id == cart.id))
    session.flush()

    if auto_commit:
        session.commit()

    session.expire(cart, ["items"])

    refreshed_cart = session.scalar(
        select(Cart)
        .where(Cart.id == cart.id)
        .options(selectinload(Cart.items).selectinload(CartItem.product))
    )
    return refreshed_cart or cart


def format_cart_response(
    cart: Cart,
    currency: str = DEFAULT_CATALOGUE_CURRENCY,
) -> CartResponse:
    """Compute trusted prices and subtotals dynamically using Decimal arithmetic.

    Does not rely on client-supplied prices.
    Uses ROUND_HALF_UP and fixed scale of 2.
    """
    item_responses: list[CartItemResponse] = []
    cart_subtotal = Decimal("0.00")
    total_quantity = 0

    for item in cart.items:
        if item.product is None:
            continue

        unit_price = Decimal(str(item.product.price)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        line_subtotal = (unit_price * Decimal(item.quantity)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        cart_subtotal += line_subtotal
        total_quantity += item.quantity

        item_responses.append(
            CartItemResponse(
                id=item.id,
                cart_id=item.cart_id,
                product_id=item.product_id,
                product_sku=item.product.sku,
                product_name=item.product.name,
                unit_price=unit_price,
                quantity=item.quantity,
                subtotal=line_subtotal,
                line_total=line_subtotal,
                created_at=item.created_at,
                updated_at=item.updated_at,
            )
        )

    return CartResponse(
        id=cart.id,
        user_id=cart.user_id,
        items=item_responses,
        subtotal=cart_subtotal.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        currency=currency,
        item_count=len(item_responses),
        total_quantity=total_quantity,
        created_at=cart.created_at,
        updated_at=cart.updated_at,
    )
