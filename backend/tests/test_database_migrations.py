from pathlib import Path
import hashlib
from uuid import uuid4

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError


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
            "guest_order_verifications",
            "guest_access_sessions",
            "guest_email_outbox",
            "auth_rate_limits",
            "user_gemini_settings",
            "gemini_usage_events",
            "admin_audit_logs",
            "ai_providers",
            "ai_models",
            "platform_ai_keys",
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
            users_table_sql = connection.scalar(
                text(
                    "SELECT sql FROM sqlite_master "
                    "WHERE type='table' AND name='users'"
                )
            )
        assert index_sql is not None
        assert index_sql.startswith(
            "CREATE UNIQUE INDEX uq_users_email_lower"
        )
        assert users_table_sql is not None
        assert "ck_users_role_valid" in users_table_sql
        assert "role IN ('customer', 'admin', 'super_admin')" in users_table_sql

        # Verify check constraint enforces role validity
        with pytest.raises(IntegrityError):
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO users (id, email, password_hash, role) "
                        "VALUES (:id, 'invalid_role@example.com', 'hash', 'invalid_role')"
                    ),
                    {"id": uuid4().hex},
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

        # Verify revision 0004 request_fingerprint, 0005 customer_email_snapshot, and 0006 lease_token columns
        orders_columns = {col["name"]: col for col in inspector.get_columns("orders")}
        assert "request_fingerprint" in orders_columns
        assert "customer_email_snapshot" in orders_columns
        outbox_columns = {col["name"]: col for col in inspector.get_columns("guest_email_outbox")}
        assert "lease_token" in outbox_columns

        # Verify multi-provider tables from revision 0011
        ai_provider_cols = {col["name"] for col in inspector.get_columns("ai_providers")}
        assert {"id", "code", "display_name", "adapter_type", "is_enabled", "priority"}.issubset(ai_provider_cols)
    finally:
        engine.dispose()

    command.downgrade(config, "base")
    verify_engine = create_engine(database_url)
    try:
        table_names = inspect(verify_engine).get_table_names()
        assert "users" not in table_names
        assert "carts" not in table_names
        assert "orders" not in table_names
        assert "ai_providers" not in table_names
        assert "ai_models" not in table_names
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
    database_path = (tmp_path / "migration-0004-test.sqlite").as_posix()
    database_url = f"sqlite+pysqlite:///{database_path}"
    monkeypatch.setenv("DATABASE_URL", database_url)

    config = Config(str(ROOT / "alembic.ini"))
    # 1. Upgrade to revision 0003
    command.upgrade(config, "0003_cart_orders")

    engine = create_engine(database_url)
    user_id = uuid4()
    order_id = uuid4()
    legacy_key = "test-legacy-idempotency-token-001"

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

    # 2. Upgrade to revision 0004
    command.upgrade(config, "0004_order_fingerprint")

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


def test_migration_0005_revision_metadata() -> None:
    import importlib.util

    path = ROOT / "backend" / "alembic" / "versions" / "0005_guest_order_verification.py"
    spec = importlib.util.spec_from_file_location("phase_5_revision_0005", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == "0005_guest_order_verification"
    assert len(module.revision) <= 32
    assert module.down_revision == "0004_order_fingerprint"


def test_migration_0005_email_snapshot_backfill_and_tables(
    tmp_path: Path, monkeypatch
) -> None:
    database_path = (tmp_path / "migration-0005-test.sqlite").as_posix()
    database_url = f"sqlite+pysqlite:///{database_path}"
    monkeypatch.setenv("DATABASE_URL", database_url)

    config = Config(str(ROOT / "alembic.ini"))
    # 1. Upgrade to revision 0004
    command.upgrade(config, "0004_order_fingerprint")

    engine = create_engine(database_url)
    user_id = uuid4()
    order_id = uuid4()
    user_email = "historical_user@example.com"

    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO users (id, email, password_hash, is_active) "
                "VALUES (:id, :email, 'hash', 1)"
            ),
            {"id": user_id.hex, "email": user_email},
        )
        conn.execute(
            text(
                "INSERT INTO orders (id, order_number, user_id, shipping_recipient_name, "
                "shipping_line1, shipping_city, shipping_region, shipping_postal_code, "
                "subtotal, shipping_fee, tax, total_amount) "
                "VALUES (:id, 'ORD-LEGACY-05', :user_id, 'Recipient', 'Line 1', "
                "'City', 'Region', '560001', 100.0, 0.0, 0.0, 100.0)"
            ),
            {"id": order_id.hex, "user_id": user_id.hex},
        )

    # 2. Upgrade to revision 0005
    command.upgrade(config, "0005_guest_order_verification")

    # 3. Verify backfilled customer_email_snapshot
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT customer_email_snapshot FROM orders WHERE id = :id"),
            {"id": order_id.hex},
        ).fetchone()
        assert row is not None
        assert row[0] == user_email

    # 4. Verify guest verification, session, and outbox tables exist
    inspector = inspect(engine)
    tables = inspector.get_table_names()
    assert "guest_order_verifications" in tables
    assert "guest_access_sessions" in tables
    assert "guest_email_outbox" in tables
    verif_cols = {c["name"] for c in inspector.get_columns("guest_order_verifications")}
    assert "delivery_failed" in verif_cols

    # 5. Verify downgrade to 0004 drops tables and drops customer_email_snapshot
    command.downgrade(config, "0004_order_fingerprint")
    downgrade_inspector = inspect(engine)
    downgrade_tables = downgrade_inspector.get_table_names()
    assert "guest_email_outbox" not in downgrade_tables
    assert "guest_order_verifications" not in downgrade_tables
    assert "guest_access_sessions" not in downgrade_tables

    cols = {c["name"] for c in downgrade_inspector.get_columns("orders")}
    assert "customer_email_snapshot" not in cols
    engine.dispose()


def test_migration_0006_revision_metadata() -> None:
    import importlib.util

    path = ROOT / "backend" / "alembic" / "versions" / "0006_outbox_lease_token.py"
    spec = importlib.util.spec_from_file_location("phase_5_revision_0006", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == "0006_outbox_lease_token"
    assert len(module.revision) <= 32
    assert module.down_revision == "0005_guest_order_verification"


def test_migration_0006_lease_token_column_and_downgrade(
    tmp_path: Path, monkeypatch
) -> None:
    database_path = (tmp_path / "migration-0006-test.sqlite").as_posix()
    database_url = f"sqlite+pysqlite:///{database_path}"
    monkeypatch.setenv("DATABASE_URL", database_url)

    config = Config(str(ROOT / "alembic.ini"))
    # Upgrade to 0005
    command.upgrade(config, "0005_guest_order_verification")
    engine = create_engine(database_url)
    inspector_0005 = inspect(engine)
    cols_0005 = {c["name"] for c in inspector_0005.get_columns("guest_email_outbox")}
    assert "lease_token" not in cols_0005

    # Upgrade to 0006
    command.upgrade(config, "0006_outbox_lease_token")
    inspector_0006 = inspect(engine)
    cols_0006 = {c["name"] for c in inspector_0006.get_columns("guest_email_outbox")}
    assert "lease_token" in cols_0006

    # Downgrade back to 0005
    command.downgrade(config, "0005_guest_order_verification")
    inspector_down = inspect(engine)
    cols_down = {c["name"] for c in inspector_down.get_columns("guest_email_outbox")}
    assert "lease_token" not in cols_down
    engine.dispose()


def test_migration_0007_revision_metadata() -> None:
    import importlib.util

    path = ROOT / "backend" / "alembic" / "versions" / "0007_auth_rate_limits.py"
    spec = importlib.util.spec_from_file_location("phase_5_revision_0007", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == "0007_auth_rate_limits"
    assert len(module.revision) <= 32
    assert module.down_revision == "0006_outbox_lease_token"


def test_migration_0007_auth_rate_limits_table_and_downgrade(
    tmp_path: Path, monkeypatch
) -> None:
    database_path = (tmp_path / "migration-0007-test.sqlite").as_posix()
    database_url = f"sqlite+pysqlite:///{database_path}"
    monkeypatch.setenv("DATABASE_URL", database_url)

    config = Config(str(ROOT / "alembic.ini"))
    # Upgrade to 0006
    command.upgrade(config, "0006_outbox_lease_token")
    engine = create_engine(database_url)
    inspector_0006 = inspect(engine)
    assert "auth_rate_limits" not in inspector_0006.get_table_names()

    # Upgrade to 0007
    command.upgrade(config, "0007_auth_rate_limits")
    inspector_0007 = inspect(engine)
    assert "auth_rate_limits" in inspector_0007.get_table_names()
    cols_0007 = {c["name"] for c in inspector_0007.get_columns("auth_rate_limits")}
    assert {"id", "key_hash", "action", "created_at", "expires_at"}.issubset(cols_0007)

    # Downgrade back to 0006
    command.downgrade(config, "0006_outbox_lease_token")
    inspector_down = inspect(engine)
    assert "auth_rate_limits" not in inspector_down.get_table_names()
    engine.dispose()


def test_migration_0008_revision_metadata() -> None:
    import importlib.util

    path = ROOT / "backend" / "alembic" / "versions" / "0008_user_gemini_settings.py"
    spec = importlib.util.spec_from_file_location("phase_6_revision_0008", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == "0008_user_gemini_settings"
    assert module.down_revision == "0007_auth_rate_limits"


def test_migration_0009_revision_metadata() -> None:
    import importlib.util

    path = ROOT / "backend" / "alembic" / "versions" / "0009_gemini_usage_events.py"
    spec = importlib.util.spec_from_file_location("phase_6_revision_0009", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == "0009_gemini_usage_events"
    assert module.down_revision == "0008_user_gemini_settings"


def test_migration_0010_revision_metadata() -> None:
    import importlib.util

    path = ROOT / "backend" / "alembic" / "versions" / "0010_admin_roles_and_audit.py"
    spec = importlib.util.spec_from_file_location("phase_6_revision_0010", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == "0010_admin_roles_and_audit"
    assert module.down_revision == "0009_gemini_usage_events"


def test_migration_0011_revision_metadata() -> None:
    import importlib.util

    path = ROOT / "backend" / "alembic" / "versions" / "0011_multi_provider_registry.py"
    spec = importlib.util.spec_from_file_location("phase_6_revision_0011", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == "0011_multi_provider_registry"
    assert module.down_revision == "0010_admin_roles_and_audit"


def test_migration_0011_tables_and_downgrade(tmp_path: Path, monkeypatch) -> None:
    database_path = (tmp_path / "migration-0011-test.sqlite").as_posix()
    database_url = f"sqlite+pysqlite:///{database_path}"
    monkeypatch.setenv("DATABASE_URL", database_url)

    config = Config(str(ROOT / "alembic.ini"))
    # Upgrade to 0010
    command.upgrade(config, "0010_admin_roles_and_audit")
    engine = create_engine(database_url)
    inspector_0010 = inspect(engine)
    assert "ai_providers" not in inspector_0010.get_table_names()

    # Upgrade to 0011
    command.upgrade(config, "0011_multi_provider_registry")
    inspector_0011 = inspect(engine)
    tables = inspector_0011.get_table_names()
    assert "ai_providers" in tables
    assert "ai_models" in tables
    assert "platform_ai_keys" in tables

    # Downgrade back to 0010
    command.downgrade(config, "0010_admin_roles_and_audit")
    inspector_down = inspect(engine)
    down_tables = inspector_down.get_table_names()
    assert "ai_providers" not in down_tables
    assert "ai_models" not in down_tables
    assert "platform_ai_keys" not in down_tables
    engine.dispose()


def test_migration_0012_revision_metadata() -> None:
    import importlib.util

    path = ROOT / "backend" / "alembic" / "versions" / "0012_users_role_check_constraint.py"
    spec = importlib.util.spec_from_file_location("phase_6_revision_0012", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == "0012_users_role_check"
    assert module.down_revision == "0011_multi_provider_registry"


def test_migration_0012_constraint_enforcement_and_downgrade(
    tmp_path: Path, monkeypatch
) -> None:
    database_path = (tmp_path / "migration-0012-test.sqlite").as_posix()
    database_url = f"sqlite+pysqlite:///{database_path}"
    monkeypatch.setenv("DATABASE_URL", database_url)

    config = Config(str(ROOT / "alembic.ini"))
    # Upgrade to 0011
    command.upgrade(config, "0011_multi_provider_registry")
    engine = create_engine(database_url)
    with engine.connect() as conn:
        sql_0011 = conn.scalar(
            text("SELECT sql FROM sqlite_master WHERE type='table' AND name='users'")
        )
        assert "ck_users_role_valid" not in (sql_0011 or "")

    # Upgrade to 0012
    command.upgrade(config, "0012_users_role_check")
    with engine.connect() as conn:
        sql_0012 = conn.scalar(
            text("SELECT sql FROM sqlite_master WHERE type='table' AND name='users'")
        )
        assert sql_0012 is not None
        assert "ck_users_role_valid" in sql_0012
        assert "role IN ('customer', 'admin', 'super_admin')" in sql_0012

        idx_sql = conn.scalar(
            text(
                "SELECT sql FROM sqlite_master "
                "WHERE type='index' AND name='uq_users_email_lower'"
            )
        )
        assert idx_sql is not None

        # Verify valid roles succeed
        with engine.begin() as insert_conn:
            insert_conn.execute(
                text(
                    "INSERT INTO users (id, email, password_hash, role) "
                    "VALUES (:id, 'admin@storeflow.test', 'h', 'admin')"
                ),
                {"id": uuid4().hex},
            )
            insert_conn.execute(
                text(
                    "INSERT INTO users (id, email, password_hash, role) "
                    "VALUES (:id, 'cust@storeflow.test', 'h', 'customer')"
                ),
                {"id": uuid4().hex},
            )

        # Verify invalid role raises IntegrityError
        with pytest.raises(IntegrityError):
            with engine.begin() as insert_conn:
                insert_conn.execute(
                    text(
                        "INSERT INTO users (id, email, password_hash, role) "
                        "VALUES (:id, 'bad@storeflow.test', 'h', 'invalid_role')"
                    ),
                    {"id": uuid4().hex},
                )

    # Downgrade back to 0011
    command.downgrade(config, "0011_multi_provider_registry")
    with engine.connect() as conn:
        sql_down = conn.scalar(
            text("SELECT sql FROM sqlite_master WHERE type='table' AND name='users'")
        )
        assert "ck_users_role_valid" not in (sql_down or "")
    engine.dispose()
