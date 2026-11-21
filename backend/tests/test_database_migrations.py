from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, inspect, text


ROOT = Path(__file__).resolve().parents[2]


def test_database_migration_upgrades_and_downgrades_sqlite(
    tmp_path: Path,
    monkeypatch,
):
    database_path = (tmp_path / "migration-test.sqlite").as_posix()
    database_url = f"sqlite+pysqlite:///{database_path}"
    monkeypatch.setenv("DATABASE_URL", database_url)

    config = Config(str(ROOT / "alembic.ini"))
    command.upgrade(config, "head")

    engine = create_engine(database_url)
    try:
        inspector = inspect(engine)
        assert {
            "users",
            "customer_profiles",
            "product_categories",
            "product_category_links",
            "products",
            "inventory",
            "addresses",
            "carts",
            "cart_items",
            "orders",
            "order_items",
            "alembic_version",
        }.issubset(set(inspector.get_table_names()))
        assert "password_hash" in {
            column["name"] for column in inspector.get_columns("users")
        }
        with engine.connect() as connection:
            index_sql = connection.scalar(
                text(
                    "SELECT sql FROM sqlite_master "
                    "WHERE type='index' AND name='uq_users_email_lower'"
                )
            )
        assert index_sql is not None
        assert index_sql.startswith(
            "CREATE UNIQUE INDEX uq_users_email_lower"
        )
        assert inspector.get_foreign_keys("addresses")[0]["options"].get(
            "ondelete"
        ) == "CASCADE"
        assert inspector.get_foreign_keys("carts")[0]["options"].get(
            "ondelete"
        ) == "CASCADE"

        # Verify orders foreign-key policies
        orders_fks = {
            fk["referred_table"]: fk for fk in inspector.get_foreign_keys("orders")
        }
        assert orders_fks["users"]["options"].get("ondelete") == "RESTRICT"
        assert orders_fks["addresses"]["options"].get("ondelete") == "SET NULL"

        # Verify redundant indexes are not created
        cart_indexes = {idx["name"] for idx in inspector.get_indexes("carts")}
        assert "ix_carts_user_id" not in cart_indexes

        cart_item_indexes = {
            idx["name"] for idx in inspector.get_indexes("cart_items")
        }
        assert "ix_cart_items_cart_id" not in cart_item_indexes
        assert "ix_cart_items_product_id" in cart_item_indexes

        order_indexes = {idx["name"] for idx in inspector.get_indexes("orders")}
        assert "ix_orders_user_id" not in order_indexes
        assert "ix_orders_order_number" in order_indexes
        assert "ix_orders_user_created" in order_indexes

        # Verify revision 0004 request_fingerprint column
        orders_columns = {col["name"]: col for col in inspector.get_columns("orders")}
        assert "request_fingerprint" in orders_columns
    finally:
        engine.dispose()

    command.downgrade(config, "base")
    verify_engine = create_engine(database_url)
    try:
        table_names = inspect(verify_engine).get_table_names()
        assert "users" not in table_names
        assert "carts" not in table_names
        assert "orders" not in table_names
    finally:
        verify_engine.dispose()


def test_migration_0003_revision_metadata() -> None:
    import importlib.util

    path = ROOT / "backend" / "alembic" / "versions" / "0003_cart_and_orders.py"
    spec = importlib.util.spec_from_file_location("phase_5_revision_0003", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == "0003_cart_orders"
    assert len(module.revision) <= 32
    assert module.down_revision == "0002_constraint_names"


def test_migration_0004_revision_metadata() -> None:
    import importlib.util

    path = ROOT / "backend" / "alembic" / "versions" / "0004_order_request_fingerprint.py"
    spec = importlib.util.spec_from_file_location("phase_5_revision_0004", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == "0004_order_fingerprint"
    assert len(module.revision) <= 32
    assert module.down_revision == "0003_cart_orders"


def test_migration_0004_fingerprint_backfill_and_constraints(
    tmp_path: Path, monkeypatch
) -> None:
    import hashlib
    from uuid import uuid4
    from sqlalchemy.exc import IntegrityError

    database_path = (tmp_path / "migration-0004-test.sqlite").as_posix()
    database_url = f"sqlite+pysqlite:///{database_path}"
    monkeypatch.setenv("DATABASE_URL", database_url)

    config = Config(str(ROOT / "alembic.ini"))
    # 1. Upgrade to revision 0003
    command.upgrade(config, "0003_cart_orders")

    engine = create_engine(database_url)
    user_id = uuid4()
    order_id = uuid4()
    legacy_key = "legacy-idempotency-key-001"

    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO users (id, email, password_hash, is_active) "
                "VALUES (:id, 'legacy@example.com', 'hash', 1)"
            ),
            {"id": user_id.hex},
        )
        conn.execute(
            text(
                "INSERT INTO orders (id, order_number, user_id, shipping_recipient_name, "
                "shipping_line1, shipping_city, shipping_region, shipping_postal_code, "
                "subtotal, shipping_fee, tax, total_amount, idempotency_key) "
                "VALUES (:id, 'ORD-LEGACY-01', :user_id, 'Recipient', 'Line 1', "
                "'City', 'Region', '560001', 100.0, 0.0, 0.0, 100.0, :key)"
            ),
            {"id": order_id.hex, "user_id": user_id.hex, "key": legacy_key},
        )

    # 2. Upgrade to head (0004_order_fingerprint)
    command.upgrade(config, "head")

    # 3. Verify backfilled request_fingerprint
    expected_hash = hashlib.sha256(
        f"legacy_backfill:{str(order_id)}:{legacy_key}".encode("utf-8")
    ).hexdigest()

    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT request_fingerprint FROM orders WHERE id = :id"),
            {"id": order_id.hex},
        ).fetchone()
        assert row is not None
        assert row[0] == expected_hash
        assert len(row[0]) == 64

        # 4. Verify check constraint: inserting an order with idempotency_key but invalid fingerprint length fails
        with pytest.raises(IntegrityError):
            with engine.begin() as insert_conn:
                insert_conn.execute(
                    text(
                        "INSERT INTO orders (id, order_number, user_id, shipping_recipient_name, "
                        "shipping_line1, shipping_city, shipping_region, shipping_postal_code, "
                        "subtotal, shipping_fee, tax, total_amount, idempotency_key, request_fingerprint) "
                        "VALUES (:id, 'ORD-FAIL-01', :user_id, 'Recipient', 'Line 1', "
                        "'City', 'Region', '560001', 50.0, 0.0, 0.0, 50.0, 'new-key', 'too-short')"
                    ),
                    {"id": uuid4().hex, "user_id": user_id.hex},
                )

    # 5. Verify downgrade to 0003 drops request_fingerprint cleanly
    command.downgrade(config, "0003_cart_orders")
    verify_inspector = inspect(engine)
    cols = {c["name"] for c in verify_inspector.get_columns("orders")}
    assert "request_fingerprint" not in cols
    engine.dispose()
