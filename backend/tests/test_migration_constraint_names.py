import importlib.util
import os
from pathlib import Path
from types import ModuleType

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import CheckConstraint, create_engine, inspect, text
from sqlalchemy.engine import Connection, make_url

from backend.app.db.base import Base
from backend.app.db import models  # noqa: F401


ROOT = Path(__file__).resolve().parents[2]
EXPECTED_CONSTRAINT_NAMES = {
    "products": "ck_products_price_nonnegative",
    "inventory": "ck_inventory_available_quantity_nonnegative",
    "addresses": "ck_addresses_country_code_length",
}
OLD_CONSTRAINT_NAMES = {
    "products": "ck_products_ck_products_price_nonnegative",
    "inventory": "ck_inventory_ck_inventory_available_quantity_nonnegative",
    "addresses": "ck_addresses_ck_addresses_country_code_length",
}


def _load_revision(filename: str, module_name: str) -> ModuleType:
    path = ROOT / "backend" / "alembic" / "versions" / filename
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load Alembic revision {filename}.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _check_constraint_definitions(connection: Connection) -> dict[str, str]:
    rows = connection.execute(
        text(
            "SELECT table_name, constraint_name, definition "
            "FROM ("
            "  SELECT table_rel.relname AS table_name, constraint_row.conname "
            "    AS constraint_name, "
            "    pg_get_constraintdef(constraint_row.oid) AS definition "
            "  FROM pg_constraint AS constraint_row "
            "  JOIN pg_class AS table_rel "
            "    ON table_rel.oid = constraint_row.conrelid "
            "  JOIN pg_namespace AS schema_row "
            "    ON schema_row.oid = table_rel.relnamespace "
            "  WHERE schema_row.nspname = 'public' "
            "    AND constraint_row.contype = 'c' "
            "    AND table_rel.relname IN ('products', 'inventory', 'addresses')"
            ") AS check_constraints"
        )
    )
    return {
        f"{table_name}.{constraint_name}": definition
        for table_name, constraint_name, definition in rows
    }


def test_model_check_constraint_names_follow_metadata_convention() -> None:
    for table_name, expected_name in EXPECTED_CONSTRAINT_NAMES.items():
        names = {
            constraint.name
            for constraint in Base.metadata.tables[table_name].constraints
            if isinstance(constraint, CheckConstraint)
        }
        assert names == {expected_name}


@pytest.mark.skipif(
    not os.environ.get("PHASE_3_1_TEST_DATABASE_URL"),
    reason="Set PHASE_3_1_TEST_DATABASE_URL to an isolated local PostgreSQL test database.",
)
def test_postgresql_migration_renames_constraints_without_changing_definitions() -> None:
    url = make_url(os.environ["PHASE_3_1_TEST_DATABASE_URL"])
    assert url.drivername == "postgresql+psycopg"
    assert url.host in {"localhost", "127.0.0.1", "::1"}
    assert url.database == "ecommerce_agent_phase3_1_test"

    engine = create_engine(url)
    revision_0001 = _load_revision(
        "0001_database_foundation.py",
        "phase_3_1_revision_0001",
    )
    revision_0002 = _load_revision(
        "0002_normalize_check_constraint_names.py",
        "phase_3_1_revision_0002",
    )

    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                assert inspect(connection).get_table_names(schema="public") == []

                with Operations.context(
                    MigrationContext.configure(
                        connection,
                        opts={"target_metadata": Base.metadata},
                    )
                ):
                    revision_0001.upgrade()

                before = _check_constraint_definitions(connection)
                expected_old = {
                    f"{table_name}.{OLD_CONSTRAINT_NAMES[table_name]}"
                    for table_name in EXPECTED_CONSTRAINT_NAMES
                }
                assert set(before) == expected_old

                # A missing old name must abort before any of the renames occur.
                connection.execute(
                    text(
                        "ALTER TABLE public.inventory "
                        "RENAME CONSTRAINT "
                        "ck_inventory_ck_inventory_available_quantity_nonnegative "
                        "TO unexpected_inventory_check"
                    )
                )
                with Operations.context(
                    MigrationContext.configure(
                        connection,
                        opts={"target_metadata": Base.metadata},
                    )
                ):
                    with pytest.raises(RuntimeError, match="Expected check constraint"):
                        revision_0002.upgrade()
                assert set(_check_constraint_definitions(connection)) == {
                    f"products.{OLD_CONSTRAINT_NAMES['products']}",
                    "inventory.unexpected_inventory_check",
                    f"addresses.{OLD_CONSTRAINT_NAMES['addresses']}",
                }

                connection.execute(
                    text(
                        "ALTER TABLE public.inventory "
                        "RENAME CONSTRAINT unexpected_inventory_check "
                        "TO ck_inventory_ck_inventory_available_quantity_nonnegative"
                    )
                )
                before = _check_constraint_definitions(connection)

                with Operations.context(
                    MigrationContext.configure(
                        connection,
                        opts={"target_metadata": Base.metadata},
                    )
                ):
                    revision_0002.upgrade()

                after = _check_constraint_definitions(connection)
                expected_new = {
                    f"{table_name}.{EXPECTED_CONSTRAINT_NAMES[table_name]}"
                    for table_name in EXPECTED_CONSTRAINT_NAMES
                }
                assert set(after) == expected_new
                assert {
                    table_name: after[f"{table_name}.{EXPECTED_CONSTRAINT_NAMES[table_name]}"]
                    for table_name in EXPECTED_CONSTRAINT_NAMES
                } == {
                    table_name: before[f"{table_name}.{OLD_CONSTRAINT_NAMES[table_name]}"]
                    for table_name in EXPECTED_CONSTRAINT_NAMES
                }
            finally:
                transaction.rollback()
    finally:
        engine.dispose()
