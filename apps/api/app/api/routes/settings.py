"""Generic read/write access to database-backed operator settings."""

from fastapi import APIRouter, HTTPException, Query, status

from app.api.dependencies import DbSession
from app.core.config import invalidate_settings_cache
from app.schemas.setup import SettingItemRead, SettingsUpdate
from app.services.settings_service import (
    SettingValidationError,
    effective_settings,
    validate_section,
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
        validate_section(effective_settings(db))
    except (SettingValidationError, RuntimeError) as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    db.commit()
    invalidate_settings_cache()
    return settings_view(db)


@router.post("/retention/preview")
def preview_retention(payload: dict, db: DbSession) -> dict:
    """"X runs would be deleted" for the values being edited (nothing is deleted)."""
    from app.services.run_retention import MODES, Policy, plan

    try:
        policy = Policy(
            mode=str(payload.get("mode") or "keep_all"),
            days=int(payload.get("days") or 180),
            keep_per_disk=int(payload.get("keep_per_disk") or 50),
            log_detail_days=int(payload.get("log_detail_days") or 0),
        )
    except (TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="invalid retention values") from None
    if policy.mode not in MODES or policy.days < 1 or policy.keep_per_disk < 1 or policy.log_detail_days < 0:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="invalid retention values")
    result = plan(db, policy)
    return {"runs": len(result.runs), "logs": len(result.logs), "protected": result.protected}
