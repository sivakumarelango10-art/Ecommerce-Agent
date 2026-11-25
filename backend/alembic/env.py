import os
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from dotenv import load_dotenv
from sqlalchemy import engine_from_config, pool

from backend.app.db.base import Base
from backend.app.db import models  # noqa: F401

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

load_dotenv(Path(__file__).resolve().parents[2] / ".env")
database_url = os.environ.get("DATABASE_URL") or config.get_main_option(
    "sqlalchemy.url"
)
if not database_url:
    raise RuntimeError("Set DATABASE_URL before running Alembic.")
config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))

target_metadata = Base.metadata


def _configure_sqlite_compatibility() -> None:
    """Ensure Alembic SQLite operations safely handle unbatched ALTER constraints.

    SQLite has no native support for ALTER TABLE ADD/DROP CONSTRAINT.
    In historical migrations where batch mode was omitted (such as 0010 adding
    ck_users_role_valid), this handler prevents NotImplementedError during direct
    ALTER operations on SQLite, while forward migration 0012 applies the constraint
    via batch_alter_table to restore full schema fidelity.
    """
    try:
        import alembic.ddl.sqlite

        orig_add = alembic.ddl.sqlite.SQLiteImpl.add_constraint
        orig_drop = alembic.ddl.sqlite.SQLiteImpl.drop_constraint

        def sqlite_safe_add_constraint(self, const, **kw):
            try:
                return orig_add(self, const, **kw)
            except NotImplementedError:
                return None

        def sqlite_safe_drop_constraint(self, const, **kw):
            try:
                return orig_drop(self, const, **kw)
            except NotImplementedError:
                return None

        alembic.ddl.sqlite.SQLiteImpl.add_constraint = sqlite_safe_add_constraint
        alembic.ddl.sqlite.SQLiteImpl.drop_constraint = sqlite_safe_drop_constraint
    except Exception:
        pass


def run_migrations_offline() -> None:
    if database_url and "sqlite" in database_url.lower():
        _configure_sqlite_compatibility()

    context.configure(
        url=database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        if connection.dialect.name == "sqlite":
            _configure_sqlite_compatibility()

        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()
    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
