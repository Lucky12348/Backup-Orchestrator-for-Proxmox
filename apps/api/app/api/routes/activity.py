"""Activity history (moved out of the removed host ntfy relay, Phase 5j)."""

from fastapi import APIRouter, Query
from sqlalchemy import select

from app.api.dependencies import DbSession
from app.models import ActivityEvent
from app.schemas.activity import ActivityEventRead

router = APIRouter(prefix="/activity-events", tags=["activity"])


@router.get("", response_model=list[ActivityEventRead])
def list_activity_events(db: DbSession, limit: int = Query(default=100, ge=1, le=500)) -> list[ActivityEventRead]:
    rows = db.scalars(select(ActivityEvent).order_by(ActivityEvent.created_at.desc(), ActivityEvent.id.desc()).limit(limit))
    return [ActivityEventRead.model_validate(item) for item in rows]


@router.get("/feed")
def activity_feed(db: DbSession, days: int = Query(default=90, ge=1, le=366)) -> list[dict]:
    """Activity page: backups, ejects, detections, planning — newest first (Phase 6)."""
    from app.services.activity_feed import build_activity

    return build_activity(db, days=days)
