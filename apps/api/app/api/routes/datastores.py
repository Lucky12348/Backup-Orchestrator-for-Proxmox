"""Datastores managed by the app (Phase 6c): source list, health, source choice."""

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api.dependencies import DbSession
from app.services import datastore_health

router = APIRouter(prefix="/datastores", tags=["datastores"])


class SourceChoice(BaseModel):
    name: str = Field(min_length=1, max_length=64)


@router.get("")
def list_datastores(db: DbSession) -> dict:
    """Source candidates from PBS (name, total, used) and the current one."""
    from app.services.settings_service import effective_settings

    try:
        items = datastore_health.list_source_candidates(db)
        error = None
    except Exception as exc:  # noqa: BLE001 - shown in the form, the field stays usable
        items, error = [], str(exc)
    return {"items": items, "current": effective_settings(db).pbs_datastore, "error": error}


@router.get("/health")
def get_health(db: DbSession) -> dict:
    return datastore_health.read_health(db) or {"checked": False, "source": None, "disks": {}, "blocked_events": [], "datastores": []}


@router.post("/health/check")
def run_check(db: DbSession) -> dict:
    return datastore_health.check(db)


@router.put("/source")
def choose_source(payload: SourceChoice, db: DbSession) -> dict:
    """"Choose another datastore": setting + plannings, then a new check."""
    return datastore_health.choose_source(db, payload.name)
