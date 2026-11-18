"""Notification channels, "Send a test", the send journal (Phase 5j).

Secrets are write-only: responses carry `secrets_set` (names of the secrets
that are stored), never a value. Errors are `{code, field}` translated by the UI.
"""

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy import select

from app.api.dependencies import DbSession
from app.models import NotificationChannel, NotificationDelivery
from app.services import notification_catalog as catalog
from app.services import notification_engine as engine
from app.services.notification_channels import (
    EMAIL_SECURITY,
    KINDS,
    ChannelError,
    apply_payload,
    channel_view,
    draft_channel,
    list_channels,
)

router = APIRouter(prefix="/notifications", tags=["notifications"])


def _invalid(exc: ChannelError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail={"code": exc.code, "field": exc.field})


def _get(db: DbSession, channel_id: int) -> NotificationChannel:
    channel = db.get(NotificationChannel, channel_id)
    if channel is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"code": "channel_not_found"})
    return channel


@router.get("/catalog")
def get_catalog() -> dict[str, Any]:
    return {
        "events": [{"key": event.key, "group": event.group, "level": event.level} for event in catalog.EVENTS],
        "groups": list(catalog.GROUPS),
        "levels": list(catalog.LEVELS),
        "kinds": {kind: [{"name": f.name, "secret": f.secret, "required": f.required} for f in fields] for kind, fields in KINDS.items()},
        "email_security": list(EMAIL_SECURITY),
    }


@router.get("/channels")
def get_channels(db: DbSession) -> list[dict[str, Any]]:
    return [channel_view(channel) for channel in list_channels(db)]


@router.post("/channels", status_code=status.HTTP_201_CREATED)
def create_channel(payload: dict[str, Any], db: DbSession) -> dict[str, Any]:
    try:
        channel = apply_payload(NotificationChannel(), payload, creating=True)
    except ChannelError as exc:
        raise _invalid(exc) from None
    db.add(channel)
    db.commit()
    db.refresh(channel)
    return channel_view(channel)


@router.put("/channels/{channel_id}")
def update_channel(channel_id: int, payload: dict[str, Any], db: DbSession) -> dict[str, Any]:
    channel = _get(db, channel_id)
    try:
        apply_payload(channel, payload, creating=False)
    except ChannelError as exc:
        db.rollback()
        raise _invalid(exc) from None
    db.commit()
    db.refresh(channel)
    return channel_view(channel)


@router.delete("/channels/{channel_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_channel(channel_id: int, db: DbSession) -> Response:
    channel = _get(db, channel_id)
    engine.cancel_channel_queue(db, channel.id)
    db.delete(channel)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/channels/{channel_id}/test")
def test_channel(channel_id: int, db: DbSession) -> dict[str, Any]:
    return engine.send_test(db, _get(db, channel_id))


@router.post("/test")
def test_draft(payload: dict[str, Any], db: DbSession) -> dict[str, Any]:
    """Test the form before saving (`channel_id` reuses the stored secrets)."""
    stored = _get(db, int(payload["channel_id"])) if payload.get("channel_id") else None
    try:
        channel = draft_channel(payload, stored)
    except ChannelError as exc:
        raise _invalid(exc) from None
    return engine.send_test(db, channel, record=False)


@router.get("/deliveries")
def get_deliveries(
    db: DbSession,
    limit: int = Query(default=100, ge=1, le=500),
    channel_id: int | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
) -> list[dict[str, Any]]:
    query = select(NotificationDelivery).order_by(NotificationDelivery.id.desc()).limit(limit)
    if channel_id is not None:
        query = query.where(NotificationDelivery.channel_id == channel_id)
    if status_filter:
        query = query.where(NotificationDelivery.status == status_filter)
    return [engine.delivery_view(item) for item in db.scalars(query)]


@router.post("/deliveries/{delivery_id}/retry")
def retry_delivery(delivery_id: int, db: DbSession) -> dict[str, Any]:
    delivery = db.get(NotificationDelivery, delivery_id)
    if delivery is None or delivery.status != "failed":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"code": "not_retryable"})
    delivery.status = "pending"
    delivery.attempts = 0
    delivery.next_attempt_at = engine._now()
    db.commit()
    engine._wake.set()
    return engine.delivery_view(delivery)
