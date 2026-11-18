import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.router import api_router
from app.api.routes.health import router as health_router
from app.auth import validate_auth_settings
from app.core.config import get_env_settings, get_settings
from app.db.init import create_tables, seed_database
from app.db.migrate import run_migrations
from app.db.preflight import DatabaseEncodingError, check_database_encoding
from app.db.session import SessionLocal, engine
from app.services import daily_tasks, notification_engine
from app.services import run_events  # registers the run.* SQLAlchemy hooks
from app.services.notification_migration import migrate_legacy_ntfy
from app.services.planning_scheduler import start_planning_scheduler, stop_planning_scheduler
from app.services.planning_titles import rename_legacy_titles
from app.services.settings_service import activate_settings_store, ensure_pbs_mode, import_settings_from_env
from app.services.users import bootstrap_admin_from_env
from app.web_static import mount_frontend, resolve_dist_dir

logger = logging.getLogger(__name__)
env_settings = get_env_settings()


def ensure_database_encoding() -> None:
    """Refuse to start on a non-UTF8 PostgreSQL database, with an explanation
    instead of the TypeError SQLAlchemy raises on its first connection."""
    try:
        check_database_encoding(env_settings.database_url)
    except DatabaseEncodingError as exc:
        logger.critical("%s", exc)
        raise SystemExit(f"Startup refused: {exc}") from None


def initialize_storage() -> None:
    """Schema + one-time imports from a legacy .env, then enable stored settings."""
    ensure_database_encoding()
    create_tables()
    run_migrations(engine)
    with SessionLocal() as db:
        import_settings_from_env(db, os.environ)
        ensure_pbs_mode(db)
        bootstrap_admin_from_env(db, os.environ)
        migrate_legacy_ntfy(db, os.environ)  # Phase 5j: one channel from the old ntfy settings
        rename_legacy_titles(db)  # B55: "Rotation tous les 1 · …" → a sentence
    activate_settings_store(SessionLocal)


@asynccontextmanager
async def lifespan(_: FastAPI):
    validate_auth_settings()
    initialize_storage()
    if env_settings.dev_seed:
        seed_database()
    settings = get_settings()
    try:
        from app.services.disk_presence import clear_mistaken_verify_flags

        with SessionLocal() as db:
            clear_mistaken_verify_flags(db)  # B49: flags set by a failure before the passthrough
    except Exception:  # noqa: BLE001 — never blocks the start
        logger.exception("Could not review the verification flags")
    start_planning_scheduler(settings)
    presence_watcher = asyncio.create_task(run_events.watch_agent_presence(SessionLocal))
    # Phase 5f.2: disks handed to the PBS VM still plugged in? + orphan usbN at start.
    from app.services import passthrough_watch

    passthrough_watcher = asyncio.create_task(passthrough_watch.watch(SessionLocal))
    # Phase 5j: notification queue (retries, quiet hours, daily summaries) and
    # the daily tasks (history retention, "N days without a backup").
    notification_engine.set_session_factory(SessionLocal)
    notification_worker = asyncio.create_task(notification_engine.watch(SessionLocal))
    daily_worker = asyncio.create_task(daily_tasks.watch(SessionLocal))
    yield
    daily_worker.cancel()
    notification_worker.cancel()
    notification_engine.set_session_factory(None)
    passthrough_watcher.cancel()
    presence_watcher.cancel()
    stop_planning_scheduler()


app = FastAPI(
    title=env_settings.app_name,
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(api_router)
app.include_router(health_router)
# Must stay last: catch-all GET that serves the SPA.
mount_frontend(app, resolve_dist_dir(env_settings.web_dist_dir))
