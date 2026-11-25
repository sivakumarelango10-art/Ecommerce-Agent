from __future__ import annotations

import argparse
import getpass
import logging
import re
import sys
from typing import Any
from uuid import uuid4

from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.config import load_settings
from backend.app.db.models import CustomerProfile, User
from backend.app.db.session import create_database_engine, create_session_factory
from backend.app.security.passwords import hash_password
from backend.app.services.admin_audit_service import record_admin_audit_event

logger = logging.getLogger(__name__)

_EMAIL_ADAPTER = TypeAdapter(EmailStr)


class AdminCreationError(Exception):
    """Raised when admin creation or promotion fails validation."""


def validate_email_address(email: str) -> str:
    """Validate email address format strictly."""
    clean = email.strip().lower()
    try:
        validated = _EMAIL_ADAPTER.validate_python(clean)
        return str(validated)
    except ValidationError:
        raise AdminCreationError(f"Invalid email address format: '{email}'") from None


def validate_admin_password(password: str) -> None:
    """Enforce the documented 12-128 character password policy."""
    if len(password) < 12:
        raise AdminCreationError("Password must be at least 12 characters long.")
    if len(password) > 128:
        raise AdminCreationError("Password must not exceed 128 characters.")


def create_or_promote_admin(
    session: Session,
    *,
    email: str,
    password: str,
    role: str = "super_admin",
    confirm_promote: bool = False,
    display_name: str = "Administrator",
) -> tuple[User, bool]:
    """Create a new administrator or promote an existing account.

    Returns (user, is_new_account).
    Raises AdminCreationError on invalid inputs or rejected promotion.
    """
    clean_email = validate_email_address(email)
    validate_admin_password(password)

    if role not in ("super_admin", "admin"):
        raise AdminCreationError(f"Unsupported administrative role: '{role}'. Must be 'admin' or 'super_admin'.")

    user = session.scalar(select(User).where(User.email == clean_email))

    if user is not None:
        if user.role == role:
            # Already has the target role
            return user, False

        if not confirm_promote:
            raise AdminCreationError(
                f"Account with email '{clean_email}' already exists with role '{user.role}'. "
                "Explicit confirmation is required to promote this account."
            )

        old_role = user.role
        user.role = role
        user.is_active = True
        user.password_hash = hash_password(password)

        record_admin_audit_event(
            session,
            actor_user_id=user.id,
            action="ADMIN_PROMOTED",
            target_type="user",
            target_id=str(user.id),
            metadata={"old_role": old_role, "new_role": role, "email": clean_email},
        )
        session.commit()
        session.refresh(user)
        return user, False

    # Create new administrator account
    hashed = hash_password(password)
    user = User(
        id=uuid4(),
        email=clean_email,
        password_hash=hashed,
        role=role,
        is_active=True,
    )
    user.profile = CustomerProfile(display_name=display_name.strip() or "Administrator")
    session.add(user)
    session.flush()

    record_admin_audit_event(
        session,
        actor_user_id=user.id,
        action="FIRST_ADMIN_CREATED" if role == "super_admin" else "ADMIN_CREATED",
        target_type="user",
        target_id=str(user.id),
        metadata={"role": role, "email": clean_email},
    )
    session.commit()
    session.refresh(user)
    return user, True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="StoreFlow First Super Admin Provisioning CLI",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--email",
        required=True,
        help="Administrator email address.",
    )
    parser.add_argument(
        "--role",
        choices=["super_admin", "admin"],
        default="super_admin",
        help="Target administrative role.",
    )
    parser.add_argument(
        "--confirm-promote",
        action="store_true",
        default=False,
        help="Confirm promotion if the account already exists.",
    )
    parser.add_argument(
        "--display-name",
        default="Administrator",
        help="Display name for the administrator profile.",
    )

    args = parser.parse_args(argv)

    try:
        clean_email = validate_email_address(args.email)
    except AdminCreationError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    try:
        p1 = getpass.getpass("Enter administrator password (min 12 characters): ")
        p2 = getpass.getpass("Confirm administrator password: ")
        if p1 != p2:
            print("Error: Passwords do not match.", file=sys.stderr)
            return 1
        password = p1
    except (KeyboardInterrupt, EOFError):
        print("\nOperation cancelled by user.", file=sys.stderr)
        return 1

    try:
        validate_admin_password(password)
    except AdminCreationError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    confirm = args.confirm_promote

    try:
        settings = load_settings()
        engine = create_database_engine(settings.database_url.get_secret_value())
        session_factory = create_session_factory(engine)
    except Exception as exc:
        print(f"Error initializing database connection: {type(exc).__name__}", file=sys.stderr)
        return 1

    try:
        with session_factory() as session:
            existing = session.scalar(select(User).where(User.email == clean_email))
            if existing is not None and existing.role != args.role and not confirm:
                # Ask interactively if running in terminal
                if sys.stdin.isatty():
                    try:
                        ans = input(
                            f"Account with email '{clean_email}' already exists with role '{existing.role}'.\n"
                            f"Promote to '{args.role}'? [y/N]: "
                        ).strip().lower()
                        if ans not in ("y", "yes"):
                            print("Promotion cancelled.", file=sys.stderr)
                            return 1
                        confirm = True
                    except (KeyboardInterrupt, EOFError):
                        print("\nOperation cancelled.", file=sys.stderr)
                        return 1
                else:
                    print(
                        f"Error: Account with email '{clean_email}' already exists. Pass --confirm-promote to promote.",
                        file=sys.stderr,
                    )
                    return 1

            user, is_new = create_or_promote_admin(
                session,
                email=clean_email,
                password=password,
                role=args.role,
                confirm_promote=confirm,
                display_name=args.display_name,
            )

            if is_new:
                print(f"Success: New {user.role} account created successfully for '{clean_email}'.")
            else:
                print(f"Success: Account '{clean_email}' is configured as {user.role}.")
            return 0
    except AdminCreationError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Database operation failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    sys.exit(main())
