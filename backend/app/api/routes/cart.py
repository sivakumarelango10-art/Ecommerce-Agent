from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from backend.app.api.auth_dependencies import get_current_user
from backend.app.api.dependencies import get_settings
from backend.app.core.config import Settings
from backend.app.db.models import User
from backend.app.db.session import get_db_session
from backend.app.schemas.cart import (
    CartItemAddRequest,
    CartItemUpdateRequest,
    CartResponse,
)
from backend.app.services.cart_service import (
    add_to_cart,
    clear_cart,
    format_cart_response,
    get_cart,
    remove_cart_item,
    update_cart_item_quantity,
)

router = APIRouter(prefix="/cart", tags=["cart"])


@router.get(
    "",
    response_model=CartResponse,
    status_code=status.HTTP_200_OK,
    summary="Get the authenticated customer's cart",
)
def get_user_cart(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> CartResponse:
    """Retrieve the current shopping cart for the authenticated user.

    Creates an empty cart automatically if none exists.
    Commits the transaction at the request boundary.
    """
    cart = get_cart(session, user.id)
    session.commit()
    return format_cart_response(cart, currency=settings.catalogue_currency)


@router.post(
    "/items",
    response_model=CartResponse,
    status_code=status.HTTP_200_OK,
    summary="Add a product to the cart",
)
def add_item_to_cart(
    body: CartItemAddRequest,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> CartResponse:
    """Add a product to the authenticated user's cart or increment quantity.

    Commits the transaction at the request boundary upon success.
    """
    cart = add_to_cart(
        session=session,
        user_id=user.id,
        product_id=body.product_id,
        quantity=body.quantity,
    )
    session.commit()
    return format_cart_response(cart, currency=settings.catalogue_currency)


@router.patch(
    "/items/{item_id}",
    response_model=CartResponse,
    status_code=status.HTTP_200_OK,
    summary="Update the quantity of an item in the cart",
)
def update_item_quantity(
    item_id: UUID,
    body: CartItemUpdateRequest,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> CartResponse:
    """Update the quantity of an existing item in the authenticated user's cart.

    Commits the transaction at the request boundary upon success.
    """
    cart = update_cart_item_quantity(
        session=session,
        user_id=user.id,
        item_id=item_id,
        quantity=body.quantity,
    )
    session.commit()
    return format_cart_response(cart, currency=settings.catalogue_currency)


@router.delete(
    "/items/{item_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Remove an item from the cart",
)
def delete_cart_item(
    item_id: UUID,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> Response:
    """Remove a specific item from the authenticated user's cart.

    Commits the transaction at the request boundary upon success.
    """
    remove_cart_item(session, user.id, item_id)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Clear the entire cart",
)
def clear_user_cart(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> Response:
    """Remove all items from the authenticated user's cart.

    Commits the transaction at the request boundary upon success.
    """
    clear_cart(session, user.id)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
