from pathlib import Path

from alembic import command
from alembic.config import Config
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
