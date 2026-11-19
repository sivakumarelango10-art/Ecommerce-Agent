from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from backend.app.api.auth_dependencies import get_current_user
from backend.app.db.models import User
from backend.app.db.session import get_db_session
from backend.app.schemas.customer import (
    AddressCreate,
    AddressResponse,
    AddressUpdate,
    CustomerProfileResponse,
    CustomerProfileUpdate,
)
from backend.app.services.customer_service import (
    create_address,
    delete_owned_address,
    get_customer_profile,
    get_owned_address,
    list_addresses,
    update_customer_profile,
    update_owned_address,
)

router = APIRouter(prefix="/customers/me", tags=["customers"])


@router.get("/profile", response_model=CustomerProfileResponse)
def get_profile(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> CustomerProfileResponse:
    profile = get_customer_profile(session, user.id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Customer profile not found.")
    return CustomerProfileResponse.model_validate(profile)


@router.put("/profile", response_model=CustomerProfileResponse)
def put_profile(
    body: CustomerProfileUpdate,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> CustomerProfileResponse:
    profile = update_customer_profile(session, user.id, body)
    if profile is None:
        raise HTTPException(status_code=404, detail="Customer profile not found.")
    return CustomerProfileResponse.model_validate(profile)


@router.get("/addresses", response_model=list[AddressResponse])
def get_addresses(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> list[AddressResponse]:
    return [
        AddressResponse.model_validate(address)
        for address in list_addresses(session, user.id)
    ]


@router.post(
    "/addresses",
    response_model=AddressResponse,
    status_code=status.HTTP_201_CREATED,
)
def post_address(
    body: AddressCreate,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> AddressResponse:
    return AddressResponse.model_validate(create_address(session, user, body))


@router.get("/addresses/{address_id}", response_model=AddressResponse)
def get_address(
    address_id: UUID,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> AddressResponse:
    address = get_owned_address(session, address_id, user.id)
    if address is None:
        raise HTTPException(status_code=404, detail="Address not found.")
    return AddressResponse.model_validate(address)


@router.put("/addresses/{address_id}", response_model=AddressResponse)
def put_address(
    address_id: UUID,
    body: AddressUpdate,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> AddressResponse:
    address = update_owned_address(session, address_id, user.id, body)
    if address is None:
        raise HTTPException(status_code=404, detail="Address not found.")
    return AddressResponse.model_validate(address)


@router.delete(
    "/addresses/{address_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def remove_address(
    address_id: UUID,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> Response:
    if not delete_owned_address(session, address_id, user.id):
        raise HTTPException(status_code=404, detail="Address not found.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
