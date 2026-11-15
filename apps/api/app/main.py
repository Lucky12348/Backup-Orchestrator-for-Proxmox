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
from app.db.session import SessionLocal, engine
from app.services.notifications import validate_notification_settings
from app.services.planning_scheduler import start_planning_scheduler, stop_planning_scheduler
from app.services.settings_service import activate_settings_store, ensure_pbs_mode, import_settings_from_env
from app.services.users import bootstrap_admin_from_env
from app.web_static import mount_frontend, resolve_dist_dir

logger = logging.getLogger(__name__)
env_settings = get_env_settings()


def initialize_storage() -> None:
    """Schema + one-time imports from a legacy .env, then enable stored settings."""
    create_tables()
    run_migrations(engine)
    with SessionLocal() as db:
        import_settings_from_env(db, os.environ)
        ensure_pbs_mode(db)
        bootstrap_admin_from_env(db, os.environ)
    activate_settings_store(SessionLocal)


@asynccontextmanager
async def lifespan(_: FastAPI):
    validate_auth_settings()
    initialize_storage()
    if env_settings.dev_seed:
        seed_database()
    settings = get_settings()
    try:
        validate_notification_settings(settings)
    except RuntimeError as exc:
        # Stored settings are fixable from the UI: report, don't refuse to start.
        logger.error("Notification settings are invalid: %s", exc)
    start_planning_scheduler(settings)
    yield
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
