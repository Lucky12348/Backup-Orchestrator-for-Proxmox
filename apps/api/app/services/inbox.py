"""The top-bar inbox (B55): every notification event, whatever the channels,
plus the alert toasts of the web UI.

Event items are stored as event + parameters and rendered in PBO's current
language when listed (services/notification_catalog.py). An identical event
inside its dedup window (the catalog's, at least 10 minutes) does not add a
line: it bumps `count` and the line is unread again. Kept as Settings ›
Retention says for the run history: "keep all" keeps them, "delete after N
days" deletes them after N days, "keep the last N per disk" after a year.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import InboxItem
from app.services import events
from app.services import notification_catalog as catalog

LEVELS = ("info", "warning", "error")
MIN_DEDUP = timedelta(minutes=10)
PER_DISK_MODE_DAYS = 365
PAGE_SIZE = 50
FILTERS = ("all", "unread", "urgent")


def _now() -> datetime:
    return datetime.now(UTC)


def _publish() -> None:
    events.publish("inbox.updated", {})


def _fold(db: Session, key: str, since: datetime, now: datetime) -> bool:
    previous = db.scalar(
        select(InboxItem).where(InboxItem.dedup_key == key, InboxItem.updated_at >= since).order_by(InboxItem.id.desc()).limit(1)
    )
    if previous is None:
        return False
    previous.count += 1
    previous.updated_at = now
    previous.read_at = None
    return True


def record_event(db: Session, event: str, level: str, params: dict[str, Any], key: str, link: str | None, now: datetime) -> None:
    """Called by notification_engine.emit() (same session, committed by it)."""
    spec = catalog.EVENTS_BY_KEY[event]
    window = max(timedelta(hours=spec.dedup_hours), MIN_DEDUP)
    if _fold(db, f"event:{key}", now - window, now):
        return
    disk_id = params.get("disk_id")
    db.add(
        InboxItem(
            source="event",
            event=event,
            level=level,
            dedup_key=f"event:{key}"[:255],
            params=params,
            link=(link or None) and link[:255],
            disk_id=disk_id if isinstance(disk_id, int) else None,
            created_at=now,
            updated_at=now,
        )
    )


def record_toast(db: Session, *, level: str, title: str, body: str | None, link: str | None, disk_id: int | None) -> InboxItem | None:
    """An alert toast of the web UI (warning / error), stored as its text."""
    now = _now()
    level = level if level in LEVELS else "warning"
    key = f"toast:{title}:{body or ''}"[:255]
    if _fold(db, key, now - MIN_DEDUP, now):
        db.commit()
        _publish()
        return None
    item = InboxItem(
        source="toast",
        event="toast",
        level=level,
        dedup_key=key,
        title=title[:255],
        body=body,
        link=link if link and link.startswith("/") else None,
        disk_id=disk_id,
        created_at=now,
        updated_at=now,
    )
    db.add(item)
    db.commit()
    _publish()
    return item


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def view(item: InboxItem, language: str) -> dict[str, Any]:
    if item.source == "event" and item.event in catalog.EVENTS_BY_KEY:
        title, body, _path = catalog.message(item.event, language, item.params or {})
        group = catalog.EVENTS_BY_KEY[item.event].group
    else:
        title, body, group = item.title or "", item.body or "", "alerts"
    return {
        "id": item.id,
        "source": item.source,
        "event": item.event,
        "group": group,
        "level": item.level,
        "title": title,
        "body": body,
        "link": item.link,
        "disk_id": item.disk_id,
        "count": item.count,
        "read": item.read_at is not None,
        "created_at": _aware(item.created_at),
        "updated_at": _aware(item.updated_at),
    }


def _filtered(query, which: str):
    if which == "unread":
        return query.where(InboxItem.read_at.is_(None))
    if which == "urgent":
        return query.where(InboxItem.level == "error")
    return query


def counts(db: Session) -> dict[str, int]:
    unread = db.scalar(select(func.count()).select_from(InboxItem).where(InboxItem.read_at.is_(None))) or 0
    urgent = db.scalar(select(func.count()).select_from(InboxItem).where(InboxItem.read_at.is_(None), InboxItem.level == "error")) or 0
    return {"unread": int(unread), "urgent_unread": int(urgent)}


def list_items(db: Session, language: str, *, which: str = "all", limit: int = PAGE_SIZE, before: int | None = None) -> dict[str, Any]:
    query = _filtered(select(InboxItem), which if which in FILTERS else "all")
    if before is not None:
        query = query.where(InboxItem.id < before)
    rows = list(db.scalars(query.order_by(InboxItem.updated_at.desc(), InboxItem.id.desc()).limit(max(1, min(limit, 200)) + 1)))
    more = len(rows) > limit
    return {"items": [view(row, language) for row in rows[:limit]], "has_more": more, **counts(db)}


def mark_read(db: Session, ids: list[int] | None) -> dict[str, int]:
    """ids None = everything."""
    statement = update(InboxItem).where(InboxItem.read_at.is_(None))
    if ids is not None:
        statement = statement.where(InboxItem.id.in_(ids))
    db.execute(statement.values(read_at=_now()).execution_options(synchronize_session=False))
    db.commit()
    _publish()
    return counts(db)


def retention_days() -> int | None:
    settings = get_settings()
    mode = getattr(settings, "run_history_mode", "keep_all")
    if mode == "days":
        return int(getattr(settings, "run_history_days", 90) or 90)
    if mode == "per_disk":
        return PER_DISK_MODE_DAYS
    return None


def purge(db: Session, now: datetime | None = None) -> int:
    days = retention_days()
    if days is None:
        return 0
    result = db.execute(
        delete(InboxItem).where(InboxItem.updated_at < (now or _now()) - timedelta(days=days)).execution_options(synchronize_session=False)
    )
    db.commit()
    return int(result.rowcount or 0)
