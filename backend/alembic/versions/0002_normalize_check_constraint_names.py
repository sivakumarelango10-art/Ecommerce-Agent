"""Normalize Phase 3 check constraint names."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_normalize_check_constraint_names"
down_revision: Union[str, None] = "0001_database_foundation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "public"
CONSTRAINT_RENAMES = (
    (
        "products",
        "ck_products_ck_products_price_nonnegative",
        "ck_products_price_nonnegative",
    ),
    (
        "inventory",
        "ck_inventory_ck_inventory_available_quantity_nonnegative",
        "ck_inventory_available_quantity_nonnegative",
    ),
    (
        "addresses",
        "ck_addresses_ck_addresses_country_code_length",
        "ck_addresses_country_code_length",
    ),
)


def _validate_constraint_names(
    connection: sa.Connection,
    renames: tuple[tuple[str, str, str], ...],
) -> None:
    inspector = sa.inspect(connection)
    for table_name, old_name, new_name in renames:
        names = {
            constraint["name"]
            for constraint in inspector.get_check_constraints(
                table_name,
                schema=SCHEMA,
            )
        }
        if old_name not in names:
            raise RuntimeError(
                f"Expected check constraint {old_name!r} on {SCHEMA}.{table_name}."
            )
        if new_name in names:
            raise RuntimeError(
                f"Target check constraint {new_name!r} already exists on "
                f"{SCHEMA}.{table_name}."
            )


def upgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name == "sqlite":
        return
    if connection.dialect.name != "postgresql":
        raise RuntimeError(
            "Constraint-name normalization supports PostgreSQL only."
        )

    _validate_constraint_names(connection, CONSTRAINT_RENAMES)
    quote = connection.dialect.identifier_preparer.quote
    for table_name, old_name, new_name in CONSTRAINT_RENAMES:
        op.execute(
            sa.text(
                f"ALTER TABLE {quote(SCHEMA)}.{quote(table_name)} "
                f"RENAME CONSTRAINT {quote(old_name)} TO {quote(new_name)}"
            )
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        return
    raise NotImplementedError(
        "Constraint-name normalization is forward-only."
    )
