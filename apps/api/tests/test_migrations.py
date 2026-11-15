from sqlalchemy import create_engine, inspect

from app.db.init import legacy_tables
from app.db.migrate import ALEMBIC_MANAGED_TABLES, run_migrations


def test_upgrade_creates_alembic_tables_and_is_idempotent(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'pbo.sqlite'}")
    run_migrations(engine)
    run_migrations(engine)  # second start: nothing to do

    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    assert {"users", "app_settings", "alembic_version"} <= tables
    assert {column["name"] for column in inspector.get_columns("users")} >= {
        "id",
        "username",
        "password_hash",
        "is_admin",
        "created_at",
        "last_login_at",
    }
    assert {column["name"] for column in inspector.get_columns("app_settings")} == {
        "key",
        "value",
        "is_secret",
        "updated_at",
    }
    engine.dispose()


def test_upgrade_on_existing_legacy_database(tmp_path):
    # Simulates an upgraded deployment: legacy tables created by create_all first.
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.sqlite'}")
    from app.db.base import Base

    Base.metadata.create_all(engine, tables=legacy_tables())
    assert not ALEMBIC_MANAGED_TABLES & set(inspect(engine).get_table_names())
    run_migrations(engine)
    assert ALEMBIC_MANAGED_TABLES <= set(inspect(engine).get_table_names())
    engine.dispose()


def test_legacy_create_all_excludes_alembic_tables():
    names = {table.name for table in legacy_tables()}
    assert "external_disks" in names
    assert not names & ALEMBIC_MANAGED_TABLES
