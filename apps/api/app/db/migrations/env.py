"""Alembic environment.

Used both by the `alembic` CLI (apps/api/alembic.ini) and programmatically by
`app.db.migrate.run_migrations()` at API startup, which passes an open
connection through `config.attributes["connection"]`.
"""

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.core.config import get_env_settings
from app.db.base import Base
from app.db.migrate import ALEMBIC_MANAGED_TABLES
import app.models  # noqa: F401  (register every model on Base.metadata)

config = context.config
target_metadata = Base.metadata


def include_object(obj, name, type_, reflected, compare_to):  # noqa: ANN001
    # Autogenerate must only consider Alembic-owned tables; legacy tables are
    # still created by app.db.init.create_tables().
    if type_ == "table":
        return name in ALEMBIC_MANAGED_TABLES
    table = getattr(obj, "table", None)
    return table is None or table.name in ALEMBIC_MANAGED_TABLES


def _configure(connection) -> None:  # noqa: ANN001
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_object=include_object,
        render_as_batch=connection.dialect.name == "sqlite",
    )


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url") or get_env_settings().database_url,
        target_metadata=target_metadata,
        include_object=include_object,
        literal_binds=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        _configure(connection)
        with context.begin_transaction():
            context.run_migrations()
        return

    section = config.get_section(config.config_ini_section) or {}
    section.setdefault("sqlalchemy.url", get_env_settings().database_url)
    engine = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with engine.connect() as conn:
        _configure(conn)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
