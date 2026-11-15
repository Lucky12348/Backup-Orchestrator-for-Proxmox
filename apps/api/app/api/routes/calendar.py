"""Planning as an iCalendar feed.

* `GET /calendar.ics?token=…` (root, public: calendar clients send no cookie).
* `/api/v1/calendar` (session): feed status, token regeneration, disable.
"""

from fastapi import APIRouter, HTTPException, Query, Request, Response, status

from app.api.dependencies import DbSession
from app.schemas.calendar import CalendarFeedRead
from app.services.calendar_feed import (
    build_calendar,
    check_calendar_token,
    current_calendar_token,
    disable_calendar_token,
    regenerate_calendar_token,
)

router = APIRouter(prefix="/calendar", tags=["calendar"])
feed_router = APIRouter(tags=["calendar"])


def _feed_url(request: Request, token: str | None) -> str | None:
    if not token:
        return None
    return f"{str(request.base_url).rstrip('/')}/calendar.ics?token={token}"


@router.get("", response_model=CalendarFeedRead)
def get_calendar_feed(request: Request, db: DbSession) -> CalendarFeedRead:
    token = current_calendar_token(db)
    return CalendarFeedRead(enabled=token is not None, url=_feed_url(request, token))


@router.post("/token", response_model=CalendarFeedRead)
def regenerate_token(request: Request, db: DbSession) -> CalendarFeedRead:
    """New token: the previous feed URL stops working immediately."""
    token = regenerate_calendar_token(db)
    return CalendarFeedRead(enabled=True, url=_feed_url(request, token))


@router.delete("/token", response_model=CalendarFeedRead)
def disable_feed(db: DbSession) -> CalendarFeedRead:
    disable_calendar_token(db)
    return CalendarFeedRead(enabled=False, url=None)


@feed_router.get("/calendar.ics", include_in_schema=False)
def get_ics(
    db: DbSession,
    token: str | None = Query(default=None, max_length=128),
    history: bool = Query(default=False),
    history_days: int = Query(default=90, ge=1, le=366),
) -> Response:
    if not check_calendar_token(db, token):
        # Same answer for "no feed" and "wrong token".
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")
    body = build_calendar(db, include_history=history, history_days=history_days)
    return Response(
        content=body,
        media_type="text/calendar; charset=utf-8",
        headers={"Cache-Control": "no-store", "Content-Disposition": 'inline; filename="pbo.ics"'},
    )
