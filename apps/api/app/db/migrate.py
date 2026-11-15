"""Programmatic Alembic upgrade, run at API startup.

Alembic owns the tables in ALEMBIC_MANAGED_TABLES. Older tables are still
created by `Base.metadata.create_all()` + the additive `ensure_*_schema()`
helpers in app/db/init.py; moving them under Alembic is a later step that
needs a baseline revision for existing deployments.
"""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Engine

ALEMBIC_MANAGED_TABLES = frozenset({"users", "app_settings", "agents", "agent_pairing_codes"})
MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def alembic_config() -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    return config


def run_migrations(engine: Engine, revision: str = "head") -> None:
    config = alembic_config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, revision)
