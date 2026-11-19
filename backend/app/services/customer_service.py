from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db.models import Address, CustomerProfile, User
from backend.app.schemas.customer import AddressCreate, AddressUpdate, CustomerProfileUpdate


def get_customer_profile(session: Session, user_id: UUID) -> CustomerProfile | None:
    return session.get(CustomerProfile, user_id)


def update_customer_profile(
    session: Session,
    user_id: UUID,
    values: CustomerProfileUpdate,
) -> CustomerProfile | None:
    profile = session.get(CustomerProfile, user_id)
    if profile is None:
        return None
    profile.display_name = values.display_name
    profile.phone = values.phone
    session.commit()
    session.refresh(profile)
    return profile


def list_addresses(session: Session, user_id: UUID) -> list[Address]:
    return list(
        session.scalars(
            select(Address)
            .where(Address.user_id == user_id)
            .order_by(Address.created_at, Address.id)
        )
    )


def create_address(
    session: Session,
    user: User,
    values: AddressCreate,
) -> Address:
    address = Address(user_id=user.id, **values.model_dump())
    session.add(address)
    session.commit()
    session.refresh(address)
    return address


def get_owned_address(
    session: Session, address_id: UUID, user_id: UUID
) -> Address | None:
    return session.scalar(
        select(Address).where(
            Address.id == address_id,
            Address.user_id == user_id,
        )
    )


def update_owned_address(
    session: Session,
    address_id: UUID,
    user_id: UUID,
    values: AddressUpdate,
) -> Address | None:
    address = get_owned_address(session, address_id, user_id)
    if address is None:
        return None
    for field, value in values.model_dump().items():
        setattr(address, field, value)
    session.commit()
    session.refresh(address)
    return address


def delete_owned_address(
    session: Session, address_id: UUID, user_id: UUID
) -> bool:
    address = get_owned_address(session, address_id, user_id)
    if address is None:
        return False
    session.delete(address)
    session.commit()
    return True
