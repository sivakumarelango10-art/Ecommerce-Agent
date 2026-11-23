from decimal import Decimal
from unittest.mock import MagicMock
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from backend.app.db.base import Base
from backend.app.db.models import Inventory, Product, ProductCategory
from backend.scripts import seed_catalogue as seed_script
from backend.scripts.seed_catalogue import (
    APPROVED_STAGING_DATABASE_NAME,
    DEMO_CATEGORIES,
    DEMO_PRODUCTS,
    STAGING_ALLOWLIST_ENV_VAR,
    STAGING_DB_NAME_ENV_VAR,
    main,
    parse_staging_database_name,
    parse_staging_hostname_allowlist,
    sanitize_database_url,
    seed_catalogue,
    validate_safe_target_database,
)


@pytest.fixture
def isolated_catalogue_db():
    """Create an isolated, in-memory SQLite database with Base schema for unit testing."""
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


# --- Core Seeding & Idempotency Tests ---

def test_seed_catalogue_initial_run_creates_all_records(isolated_catalogue_db: Session):
    """Seed script populates all categories, products, and inventory items with constraints met."""
    session = isolated_catalogue_db

    summary = seed_catalogue(session, dry_run=False)

    assert summary.is_dry_run is False
    assert summary.categories_created == len(DEMO_CATEGORIES)
    assert summary.categories_existing == 0
    assert summary.products_created == len(DEMO_PRODUCTS)
    assert summary.products_skipped == 0
    assert summary.inventory_created == len(DEMO_PRODUCTS)

    # Verify database counts
    cat_count = session.scalar(select(func.count(ProductCategory.id)))
    prod_count = session.scalar(select(func.count(Product.id)))
    inv_count = session.scalar(select(func.count(Inventory.product_id)))

    assert cat_count == len(DEMO_CATEGORIES)
    assert prod_count == len(DEMO_PRODUCTS)
    assert inv_count == len(DEMO_PRODUCTS)

    # Verify model invariants: active status, positive price, positive inventory, linked category
    products = list(session.scalars(select(Product)).all())
    assert len(products) == len(DEMO_PRODUCTS)
    for p in products:
        assert p.is_active is True
        assert p.price > Decimal("0")
        assert len(p.categories) >= 1
        assert p.inventory is not None
        assert p.inventory.available_quantity > 0


def test_seed_catalogue_idempotency_running_twice_skips_duplicates(isolated_catalogue_db: Session):
    """Running seed_catalogue twice does not duplicate or overwrite products or categories."""
    session = isolated_catalogue_db

    # First run: creates all records
    summary1 = seed_catalogue(session, dry_run=False)
    assert summary1.products_created == len(DEMO_PRODUCTS)

    # Second run: should recognize existing records and skip all products
    summary2 = seed_catalogue(session, dry_run=False)
    assert summary2.categories_created == 0
    assert summary2.categories_existing == len(DEMO_CATEGORIES)
    assert summary2.products_created == 0
    assert summary2.products_skipped == len(DEMO_PRODUCTS)
    assert summary2.inventory_created == 0

    # Ensure total record counts did not change
    prod_count = session.scalar(select(func.count(Product.id)))
    cat_count = session.scalar(select(func.count(ProductCategory.id)))
    inv_count = session.scalar(select(func.count(Inventory.product_id)))

    assert prod_count == len(DEMO_PRODUCTS)
    assert cat_count == len(DEMO_CATEGORIES)
    assert inv_count == len(DEMO_PRODUCTS)


def test_seed_catalogue_dry_run_does_not_persist_records(isolated_catalogue_db: Session):
    """Dry run validates all operations and relationships, then rolls back without persisting."""
    session = isolated_catalogue_db

    summary = seed_catalogue(session, dry_run=True)

    assert summary.is_dry_run is True
    assert summary.products_created == len(DEMO_PRODUCTS)
    assert summary.inventory_created == len(DEMO_PRODUCTS)

    # Verify no records were committed to the database
    prod_count = session.scalar(select(func.count(Product.id)))
    cat_count = session.scalar(select(func.count(ProductCategory.id)))
    inv_count = session.scalar(select(func.count(Inventory.product_id)))

    assert prod_count == 0
    assert cat_count == 0
    assert inv_count == 0


# --- Hostname Allowlist & Database Target Validation Tests ---

def test_approved_staging_hostname_accepted():
    """Approved PostgreSQL staging hostname is validated successfully."""
    url = "postgresql+psycopg://test_user:test_password@staging-db.example.test/neondb?sslmode=require"
    parsed = validate_safe_target_database(
        url,
        approved_hosts="staging-db.example.test",
        approved_db_name="neondb",
    )

    assert parsed.host == "staging-db.example.test"
    assert parsed.drivername == "postgresql+psycopg"


def test_wrong_hostname_rejected_including_production():
    """Target URLs with wrong or production hostnames are strictly rejected."""
    prod_url = "postgresql+psycopg://test_user:test_password@prod-db.example.test/neondb"
    with pytest.raises(ValueError, match="is not in the approved staging allowlist"):
        validate_safe_target_database(
            prod_url,
            approved_hosts="staging-db.example.test",
            approved_db_name="neondb",
        )

    other_url = "postgresql+psycopg://test_user:test_password@unapproved-db.example.test/neondb"
    with pytest.raises(ValueError, match="is not in the approved staging allowlist"):
        validate_safe_target_database(
            other_url,
            approved_hosts="staging-db.example.test",
            approved_db_name="neondb",
        )


def test_missing_or_empty_allowlist_rejected():
    """Rejects validation when the staging allowlist environment variable is missing or empty."""
    with pytest.raises(ValueError, match="is absent"):
        parse_staging_hostname_allowlist(None)

    with pytest.raises(ValueError, match="is empty"):
        parse_staging_hostname_allowlist("")

    with pytest.raises(ValueError, match="is empty"):
        parse_staging_hostname_allowlist("   ")


def test_malformed_allowlist_rejected():
    """Rejects malformed allowlist formats such as invalid characters or empty segments."""
    with pytest.raises(ValueError, match="malformed"):
        parse_staging_hostname_allowlist("host1, ,host2")

    with pytest.raises(ValueError, match="malformed hostname"):
        parse_staging_hostname_allowlist("staging-db.example.test:5432")

    with pytest.raises(ValueError, match="malformed hostname"):
        parse_staging_hostname_allowlist("staging-db.example.test/path")

    with pytest.raises(ValueError, match="malformed hostname"):
        parse_staging_hostname_allowlist("test_user@staging-db.example.test")


# --- Database Name Allowlist & Target Tests ---

def test_exact_staging_database_name_accepted(monkeypatch: pytest.MonkeyPatch):
    """Exact staging database name ('neondb') is accepted and normalized, including case-insensitively."""
    # Test parse_staging_database_name normalization to canonical constant
    assert parse_staging_database_name("neondb") == APPROVED_STAGING_DATABASE_NAME
    assert parse_staging_database_name("NEONDB") == APPROVED_STAGING_DATABASE_NAME
    assert parse_staging_database_name("  NeoNDb  ") == APPROVED_STAGING_DATABASE_NAME

    url_lower = "postgresql+psycopg://test_user:test_password@staging-db.example.test/neondb"
    parsed_lower = validate_safe_target_database(
        url_lower,
        approved_hosts="staging-db.example.test",
        approved_db_name="neondb",
    )
    assert parsed_lower.database == "neondb"

    url_upper = "postgresql+psycopg://test_user:test_password@staging-db.example.test/NEONDB"
    parsed_upper = validate_safe_target_database(
        url_upper,
        approved_hosts="staging-db.example.test",
        approved_db_name="NEONDB",
    )
    assert parsed_upper.database == "NEONDB"

    # Also test resolution directly from environment variables
    monkeypatch.setenv(STAGING_ALLOWLIST_ENV_VAR, "staging-db.example.test")
    monkeypatch.setenv(STAGING_DB_NAME_ENV_VAR, "neondb")
    parsed_env = validate_safe_target_database(url_lower)
    assert parsed_env.database == "neondb"


def test_production_db_and_postgres_rejected():
    """parse_staging_database_name strictly rejects production_db and postgres."""
    with pytest.raises(ValueError, match="is not the approved staging database"):
        parse_staging_database_name("production_db")

    with pytest.raises(ValueError, match="is not the approved staging database"):
        parse_staging_database_name("postgres")


def test_direct_approved_db_name_override_cannot_bypass_restriction():
    """validate_safe_target_database strictly prevents callers from bypassing the approved db restriction."""
    # Passing production_db directly as approved_db_name
    with pytest.raises(ValueError, match="is not the approved staging database"):
        validate_safe_target_database(
            "postgresql+psycopg://test_user:test_password@staging-db.example.test/production_db",
            approved_hosts="staging-db.example.test",
            approved_db_name="production_db",
        )

    # Passing postgres directly as approved_db_name
    with pytest.raises(ValueError, match="is not the approved staging database"):
        validate_safe_target_database(
            "postgresql+psycopg://test_user:test_password@staging-db.example.test/postgres",
            approved_hosts="staging-db.example.test",
            approved_db_name="postgres",
        )

    # Passing protected dev database directly as approved_db_name
    with pytest.raises(ValueError, match="is a protected development or test database"):
        validate_safe_target_database(
            "postgresql+psycopg://test_user:test_password@staging-db.example.test/ecommerce_agent_dev",
            approved_hosts="staging-db.example.test",
            approved_db_name="ecommerce_agent_dev",
        )


def test_wrong_database_name_rejected_on_correct_hostname():
    """Target URL with an unapproved database name on an approved host is strictly rejected."""
    prod_url = "postgresql+psycopg://test_user:test_password@staging-db.example.test/production_db"
    with pytest.raises(ValueError, match="Safety violation: Target database name 'production_db' does not match"):
        validate_safe_target_database(
            prod_url,
            approved_hosts="staging-db.example.test",
            approved_db_name="neondb",
        )

    postgres_url = "postgresql+psycopg://test_user:test_password@staging-db.example.test/postgres"
    with pytest.raises(ValueError, match="Safety violation: Target database name 'postgres' does not match"):
        validate_safe_target_database(
            postgres_url,
            approved_hosts="staging-db.example.test",
            approved_db_name="neondb",
        )


def test_missing_or_empty_database_name_allowlist_rejected(monkeypatch: pytest.MonkeyPatch):
    """Rejects validation when STORE_FLOW_STAGING_DATABASE_NAME is absent or empty."""
    with pytest.raises(ValueError, match="is absent"):
        parse_staging_database_name(None)

    with pytest.raises(ValueError, match="is empty"):
        parse_staging_database_name("")

    with pytest.raises(ValueError, match="is empty"):
        parse_staging_database_name("   ")

    # In validate_safe_target_database when env var is missing
    monkeypatch.setenv(STAGING_ALLOWLIST_ENV_VAR, "staging-db.example.test")
    monkeypatch.delenv(STAGING_DB_NAME_ENV_VAR, raising=False)
    with pytest.raises(ValueError, match="is absent"):
        validate_safe_target_database("postgresql+psycopg://test_user:test_password@staging-db.example.test/neondb")


def test_malformed_database_name_rejected():
    """Rejects database names containing invalid characters (slashes, colons, spaces, etc.)."""
    with pytest.raises(ValueError, match="malformed"):
        parse_staging_database_name("neondb/orders")

    with pytest.raises(ValueError, match="malformed"):
        parse_staging_database_name("neondb:5432")

    with pytest.raises(ValueError, match="malformed"):
        parse_staging_database_name("neon db")


def test_protected_dev_and_test_databases_rejected():
    """Rejects target databases named ecommerce_agent_dev or ecommerce_agent_test even on approved host."""
    dev_url = "postgresql+psycopg://test_user:test_password@staging-db.example.test/ecommerce_agent_dev"
    with pytest.raises(ValueError, match="Safety violation: Target database.*ecommerce_agent_dev"):
        validate_safe_target_database(
            dev_url,
            approved_hosts="staging-db.example.test",
            approved_db_name="neondb",
        )

    test_url = "postgresql+psycopg://test_user:test_password@staging-db.example.test/ecommerce_agent_test"
    with pytest.raises(ValueError, match="Safety violation: Target database.*ecommerce_agent_test"):
        validate_safe_target_database(
            test_url,
            approved_hosts="staging-db.example.test",
            approved_db_name="neondb",
        )

    query_dev_url = "postgresql+psycopg://test_user:test_password@staging-db.example.test/neondb?target=ecommerce_agent_dev"
    with pytest.raises(ValueError, match="Safety violation: Target database.*ecommerce_agent_dev"):
        validate_safe_target_database(
            query_dev_url,
            approved_hosts="staging-db.example.test",
            approved_db_name="neondb",
        )

    with pytest.raises(ValueError, match="is a protected development or test database"):
        parse_staging_database_name("ecommerce_agent_dev")

    with pytest.raises(ValueError, match="is a protected development or test database"):
        parse_staging_database_name("ecommerce_agent_test")


def test_non_postgresql_scheme_rejected():
    """Strictly rejects non-PostgreSQL drivers (such as SQLite or MySQL)."""
    with pytest.raises(ValueError, match="is not PostgreSQL"):
        validate_safe_target_database(
            "sqlite+pysqlite:///:memory:",
            approved_hosts="staging-db.example.test",
            approved_db_name="neondb",
        )

    with pytest.raises(ValueError, match="is not PostgreSQL"):
        validate_safe_target_database(
            "mysql+pymysql://test_user:test_password@staging-db.example.test/neondb",
            approved_hosts="staging-db.example.test",
            approved_db_name="neondb",
        )


# --- CLI Safety & Configuration Mismatch Tests ---

def test_database_url_and_unpooled_mismatch_rejected(monkeypatch: pytest.MonkeyPatch, capsys):
    """When both DATABASE_URL and DATABASE_URL_UNPOOLED are set, mismatch or unapproved host is rejected."""
    # Staging allowlist allows only staging-db.example.test and neondb
    monkeypatch.setenv(STAGING_ALLOWLIST_ENV_VAR, "staging-db.example.test")
    monkeypatch.setenv(STAGING_DB_NAME_ENV_VAR, "neondb")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://test_user:test_password@staging-db.example.test/neondb")
    # Unpooled is pointing to an unapproved production host
    monkeypatch.setenv("DATABASE_URL_UNPOOLED", "postgresql+psycopg://test_user:test_password@prod-db.example.test/neondb")
    monkeypatch.setattr("sys.argv", ["seed_catalogue.py", "--confirm-staging"])

    exit_code = main()

    assert exit_code == 2
    captured = capsys.readouterr()
    assert "Safety violation" in captured.err
    assert "prod-db.example.test" in captured.err


def test_explicit_database_url_override_mismatch_rejected(monkeypatch: pytest.MonkeyPatch, capsys):
    """CLI rejects explicit --database-url if its hostname or database name is not approved."""
    monkeypatch.setenv(STAGING_ALLOWLIST_ENV_VAR, "staging-db.example.test")
    monkeypatch.setenv(STAGING_DB_NAME_ENV_VAR, "neondb")

    # Unapproved host
    monkeypatch.setattr(
        "sys.argv",
        [
            "seed_catalogue.py",
            "--confirm-staging",
            "--database-url",
            "postgresql+psycopg://test_user:test_password@unapproved-db.example.test/neondb",
        ],
    )
    exit_code = main()
    assert exit_code == 2
    captured = capsys.readouterr()
    assert "Safety violation" in captured.err
    assert "unapproved-db.example.test" in captured.err

    # Approved host but wrong database name
    monkeypatch.setattr(
        "sys.argv",
        [
            "seed_catalogue.py",
            "--confirm-staging",
            "--database-url",
            "postgresql+psycopg://test_user:test_password@staging-db.example.test/prod_db",
        ],
    )
    exit_code2 = main()
    assert exit_code2 == 2
    captured2 = capsys.readouterr()
    assert "Safety violation" in captured2.err
    assert "prod_db" in captured2.err


def test_live_execution_requires_confirm_staging(monkeypatch: pytest.MonkeyPatch, capsys):
    """CLI fails if running in live mode without --confirm-staging."""
    monkeypatch.setenv(STAGING_ALLOWLIST_ENV_VAR, "staging-db.example.test")
    monkeypatch.setenv(STAGING_DB_NAME_ENV_VAR, "neondb")
    monkeypatch.setattr("sys.argv", ["seed_catalogue.py"])

    exit_code = main()

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "requires explicit staging confirmation" in captured.err
    assert "--confirm-staging" in captured.err


def test_no_database_engine_constructed_when_validation_fails(monkeypatch: pytest.MonkeyPatch):
    """Strictly ensures create_database_engine is never called if target validation fails."""
    mock_create_engine = MagicMock()
    monkeypatch.setattr(seed_script, "create_database_engine", mock_create_engine)

    # 1. Missing hostname allowlist
    monkeypatch.delenv(STAGING_ALLOWLIST_ENV_VAR, raising=False)
    monkeypatch.setenv(STAGING_DB_NAME_ENV_VAR, "neondb")
    monkeypatch.setattr(
        "sys.argv",
        [
            "seed_catalogue.py",
            "--confirm-staging",
            "--database-url",
            "postgresql+psycopg://test_user:test_password@staging-db.example.test/neondb",
        ],
    )
    exit_code = main()
    assert exit_code == 2
    assert mock_create_engine.call_count == 0

    # 2. Missing database name allowlist
    monkeypatch.setenv(STAGING_ALLOWLIST_ENV_VAR, "staging-db.example.test")
    monkeypatch.delenv(STAGING_DB_NAME_ENV_VAR, raising=False)
    monkeypatch.setattr(
        "sys.argv",
        [
            "seed_catalogue.py",
            "--confirm-staging",
            "--database-url",
            "postgresql+psycopg://test_user:test_password@staging-db.example.test/neondb",
        ],
    )
    exit_code = main()
    assert exit_code == 2
    assert mock_create_engine.call_count == 0

    # 3. Wrong host
    monkeypatch.setenv(STAGING_ALLOWLIST_ENV_VAR, "staging-db.example.test")
    monkeypatch.setenv(STAGING_DB_NAME_ENV_VAR, "neondb")
    monkeypatch.setattr(
        "sys.argv",
        [
            "seed_catalogue.py",
            "--confirm-staging",
            "--database-url",
            "postgresql+psycopg://test_user:test_password@unapproved-db.example.test/neondb",
        ],
    )
    exit_code = main()
    assert exit_code == 2
    assert mock_create_engine.call_count == 0

    # 4. Wrong database name on approved host
    monkeypatch.setattr(
        "sys.argv",
        [
            "seed_catalogue.py",
            "--confirm-staging",
            "--database-url",
            "postgresql+psycopg://test_user:test_password@staging-db.example.test/production_db",
        ],
    )
    exit_code = main()
    assert exit_code == 2
    assert mock_create_engine.call_count == 0

    # 5. Protected dev database on approved host
    monkeypatch.setattr(
        "sys.argv",
        [
            "seed_catalogue.py",
            "--confirm-staging",
            "--database-url",
            "postgresql+psycopg://test_user:test_password@staging-db.example.test/ecommerce_agent_dev",
        ],
    )
    exit_code = main()
    assert exit_code == 2
    assert mock_create_engine.call_count == 0

    # 6. Environment variable override attempt with unapproved db name
    monkeypatch.setenv(STAGING_DB_NAME_ENV_VAR, "production_db")
    monkeypatch.setattr(
        "sys.argv",
        [
            "seed_catalogue.py",
            "--confirm-staging",
            "--database-url",
            "postgresql+psycopg://test_user:test_password@staging-db.example.test/production_db",
        ],
    )
    exit_code = main()
    assert exit_code == 2
    assert mock_create_engine.call_count == 0

    # 7. Unapproved database name 'postgres' on approved host
    monkeypatch.setenv(STAGING_DB_NAME_ENV_VAR, "neondb")
    monkeypatch.setattr(
        "sys.argv",
        [
            "seed_catalogue.py",
            "--confirm-staging",
            "--database-url",
            "postgresql+psycopg://test_user:test_password@staging-db.example.test/postgres",
        ],
    )
    exit_code = main()
    assert exit_code == 2
    assert mock_create_engine.call_count == 0


def test_sanitize_database_url_hides_credentials():
    """Sanitize URL masks passwords and secrets."""
    test_password = "synthetic_test_password_to_mask"
    secret_url = f"postgresql+psycopg://test_user:{test_password}@staging-db.example.test/neondb"
    sanitized = sanitize_database_url(secret_url)

    assert test_password not in sanitized
    assert "test_user" in sanitized
    assert "staging-db.example.test" in sanitized


def test_dry_run_console_notice(monkeypatch: pytest.MonkeyPatch, capsys):
    """Dry-run explicitly prints notice that transactional inserts will be executed and rolled back."""
    mock_create_engine = MagicMock()
    mock_session = MagicMock()
    mock_factory = MagicMock(return_value=mock_session)
    mock_session.__enter__.return_value = mock_session
    mock_session.__exit__.return_value = False

    monkeypatch.setattr(seed_script, "create_database_engine", mock_create_engine)
    monkeypatch.setattr(seed_script, "create_session_factory", lambda e: mock_factory)
    monkeypatch.setattr(
        seed_script,
        "seed_catalogue",
        lambda s, dry_run: seed_script.SeedSummary(
            categories_created=6,
            categories_existing=0,
            products_created=26,
            products_skipped=0,
            inventory_created=26,
            is_dry_run=True,
        ),
    )

    monkeypatch.setenv(STAGING_ALLOWLIST_ENV_VAR, "staging-db.example.test")
    monkeypatch.setenv(STAGING_DB_NAME_ENV_VAR, "neondb")
    test_password = "synthetic_dry_run_password_to_mask"
    monkeypatch.setattr(
        "sys.argv",
        [
            "seed_catalogue.py",
            "--dry-run",
            "--database-url",
            f"postgresql+psycopg://test_user:{test_password}@staging-db.example.test/neondb",
        ],
    )

    exit_code = main()

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "[DRY-RUN EXECUTION]" in captured.out
    assert "Connecting to the staging database and executes transactional inserts" in captured.out or "connects to the staging database" in captured.out
    assert "rolled back" in captured.out
    assert test_password not in captured.out
