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
    finally:
        engine.dispose()

    command.downgrade(config, "base")
    verify_engine = create_engine(database_url)
    try:
        assert "users" not in inspect(verify_engine).get_table_names()
    finally:
        verify_engine.dispose()
