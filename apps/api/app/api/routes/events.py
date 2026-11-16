"""GET /api/v1/events: Server-Sent Events (session cookie, like every
protected route). See app/services/events.py."""

from fastapi import APIRouter, Header, Request
from fastapi.responses import StreamingResponse

from app.services import events

router = APIRouter(tags=["events"])


@router.get("/events")
async def stream_events(
    request: Request,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    # EventSource sends Last-Event-ID itself when it reconnects; the query
    # parameter lets a page that was reloaded resume as well.
    resume_from = events.parse_last_event_id(last_event_id or request.query_params.get("lastEventId"))
    return StreamingResponse(
        events.event_stream(events.bus, resume_from, is_disconnected=request.is_disconnected),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",  # nginx & co: do not buffer the stream
            "Connection": "keep-alive",
        },
    )
