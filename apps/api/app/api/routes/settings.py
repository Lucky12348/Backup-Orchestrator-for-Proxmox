"""Generic read/write access to database-backed operator settings."""

from fastapi import APIRouter, HTTPException, Query, status

from app.api.dependencies import DbSession
from app.core.config import invalidate_settings_cache
from app.schemas.setup import SettingItemRead, SettingsUpdate
from app.services.notifications import validate_notification_settings
from app.services.settings_service import (
    SettingValidationError,
    effective_settings,
    settings_view,
    update_settings,
)

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("", response_model=list[SettingItemRead])
def list_settings(db: DbSession, group: list[str] | None = Query(default=None)) -> list[dict]:
    return settings_view(db, group)


@router.patch("", response_model=list[SettingItemRead])
def patch_settings(payload: SettingsUpdate, db: DbSession) -> list[dict]:
    try:
        update_settings(db, payload.values, commit=False)
        db.flush()
        validate_notification_settings(effective_settings(db))
    except (SettingValidationError, RuntimeError) as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    db.commit()
    invalidate_settings_cache()
    return settings_view(db)
