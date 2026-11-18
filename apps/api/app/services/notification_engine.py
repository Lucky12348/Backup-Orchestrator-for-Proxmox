"""The notification engine (Phase 5j): one queue, one sender (Apprise).

`emit()` turns an event into one delivery per matching channel (event chosen,
level ≥ the channel's minimum), folds an identical alert into the previous one
(same dedup key inside the event's window), holds non-errors during quiet
hours and, for a daily-summary channel, until the summary. The worker
(`watch()`, started in the lifespan) sends what is due, retries failures with
a growing delay, gives up with a readable error, sends the daily summaries
and trims the journal. `emit()` never raises into the caller.
"""

from __future__ import annotations

import asyncio
import logging
import re
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from datetime import time as dtime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import NotificationChannel, NotificationDelivery, User
from app.services import notification_catalog as catalog
from app.services.notification_channels import (
    ChannelError,
    apprise_url,
    read_secrets,
    redact,
)

logger = logging.getLogger(__name__)

# Retry delays after a failed attempt; after the last one the delivery fails.
RETRY_DELAYS = (timedelta(minutes=1), timedelta(minutes=5), timedelta(minutes=15), timedelta(hours=1))
MAX_ATTEMPTS = len(RETRY_DELAYS) + 1
JOURNAL_DAYS = 30
WORKER_INTERVAL_SECONDS = 15
NOTIFY_TYPE = {"info": "info", "warning": "warning", "error": "failure", "success": "success"}

# Replaced in tests (conftest): None = emit() does nothing.
_session_factory: Callable[[], Session] | None = None
_wake = threading.Event()


def set_session_factory(factory: Callable[[], Session] | None) -> None:
    global _session_factory
    _session_factory = factory


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _zone() -> ZoneInfo:
    try:
        return ZoneInfo(get_settings().timezone or "UTC")
    except Exception:  # noqa: BLE001 — a bad zone must not stop notifications
        return ZoneInfo("UTC")


def admin_language(db: Session) -> str:
    language = db.scalar(select(User.language).order_by(User.id).limit(1))
    return language if language in ("en", "fr") else "en"


def public_link(path: str | None) -> str | None:
    base = (get_settings().public_url or "").strip().rstrip("/")
    if not base or not path:
        return None
    if path.endswith("/"):  # "/disks/{disk_id}" without a disk
        path = path.rstrip("/") or "/"
    return base + path


# --- quiet hours / digest --------------------------------------------------------------------


def _hm(value: str) -> dtime:
    hours, minutes = value.split(":")
    return dtime(int(hours), int(minutes))


def quiet_until(channel: NotificationChannel, now: datetime, zone: ZoneInfo) -> datetime | None:
    """End of the current quiet period (UTC) when `now` is inside it, else None."""
    if not channel.quiet_start or not channel.quiet_end:
        return None
    local = now.astimezone(zone)
    start, end = _hm(channel.quiet_start), _hm(channel.quiet_end)
    current = local.time()
    inside = start <= current < end if start < end else current >= start or current < end
    if not inside:
        return None
    end_day = local.date()
    if start > end and current >= start:  # overnight window, before midnight
        end_day += timedelta(days=1)
    return datetime.combine(end_day, end, tzinfo=zone).astimezone(UTC)


def digest_due(channel: NotificationChannel, now: datetime, zone: ZoneInfo) -> bool:
    local = now.astimezone(zone)
    if local.time() < _hm(channel.digest_time or "08:00"):
        return False
    last = _aware(channel.last_digest_at)
    return last is None or last.astimezone(zone).date() < local.date()


# --- emit ------------------------------------------------------------------------------------


def emit(
    event: str,
    *,
    params: dict[str, Any] | None = None,
    level: str | None = None,
    disk: Any = None,
    dedup: str | None = None,
    db: Session | None = None,
) -> int:
    """Queue `event` for every matching channel. Returns the number of new deliveries."""
    if db is None and _session_factory is None:
        return 0
    try:
        if db is not None:
            return _emit(db, event, params or {}, level, disk, dedup)
        with _session_factory() as session:  # type: ignore[misc]
            return _emit(session, event, params or {}, level, disk, dedup)
    except Exception:
        logger.exception("Notification %s could not be queued", event)
        return 0


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _json_value(v) for k, v in value.items()}
    return str(value)


def render_delivery(delivery: NotificationDelivery, language: str) -> tuple[str, str]:
    """Title and body in `language` (B55); the stored text for old rows and tests."""
    if delivery.params is not None and delivery.event in catalog.EVENTS_BY_KEY:
        title, body, _path = catalog.message(delivery.event, language, delivery.params)
        return title[:255], body
    if delivery.event == "test":
        return catalog.TEST_MESSAGE[language]
    spec = catalog.EVENTS_BY_KEY.get(delivery.event)
    if spec is not None and set(_PLACEHOLDER.findall(spec.title["en"])) <= {"kind"}:
        # A row from before B55 (no parameters): its title needs none, so it
        # follows the language too; the body keeps the text it was sent with.
        return catalog.message(delivery.event, language, {})[0][:255], delivery.body
    return delivery.title, delivery.body


_PLACEHOLDER = re.compile(r"\{(\w+)\}")


def _emit(db: Session, event: str, params: dict[str, Any], level: str | None, disk: Any, dedup: str | None) -> int:
    from app.services import inbox

    spec = catalog.EVENTS_BY_KEY[event]
    level = level or spec.level
    values = {key: _json_value(value) for key, value in params.items()}
    if disk is not None:
        values.setdefault("disk_id", getattr(disk, "id", None))
        values.setdefault("disk", catalog.disk_param(getattr(disk, "display_name", None) or getattr(disk, "serial_number", None), getattr(disk, "color", None)))
    path = catalog.link_path(event, values)
    key = f"{event}:{dedup if dedup is not None else values.get('disk_id', '')}"[:255]
    now = _now()
    # The inbox gets every event, whatever the channels (B55).
    inbox.record_event(db, event, level, values, key, path, now)
    channels = [
        channel
        for channel in db.scalars(select(NotificationChannel).where(NotificationChannel.enabled.is_(True)))
        if event in (channel.events or []) and catalog.level_allows(level, channel.min_level)
    ]
    if not channels:
        db.commit()
        inbox._publish()
        return 0
    language = admin_language(db)
    title, body, _path = catalog.message(event, language, values)
    link = public_link(path)
    zone = _zone()
    created = 0
    for channel in channels:
        if spec.dedup_hours and _fold_duplicate(db, channel.id, key, now - timedelta(hours=spec.dedup_hours)):
            continue
        delivery = NotificationDelivery(
            channel_id=channel.id,
            channel_name=channel.name,
            event=event,
            level=level,
            dedup_key=key,
            title=title[:255],
            body=body,
            link=link,
            params=values,
            status="pending",
            next_attempt_at=now,
            created_at=now,
        )
        if level != "error":
            held = quiet_until(channel, now, zone)
            if channel.digest:
                delivery.status = "digest"
                delivery.next_attempt_at = None
            elif held is not None:
                delivery.next_attempt_at = held
        db.add(delivery)
        created += 1
    db.commit()
    inbox._publish()
    if created:
        _wake.set()
    return created


def _fold_duplicate(db: Session, channel_id: int, key: str, since: datetime) -> bool:
    previous = db.scalar(
        select(NotificationDelivery)
        .where(
            NotificationDelivery.channel_id == channel_id,
            NotificationDelivery.dedup_key == key,
            NotificationDelivery.created_at >= since,
            NotificationDelivery.status.in_(("pending", "digest", "sent")),
        )
        .order_by(NotificationDelivery.id.desc())
        .limit(1)
    )
    if previous is None:
        return False
    previous.duplicates += 1
    return True


# --- sending ---------------------------------------------------------------------------------


def _apprise_notify(url: str, title: str, body: str, notify_type: str) -> tuple[bool, str | None]:
    """One message through Apprise; (ok, readable error). Replaced in tests."""
    import apprise

    sender = apprise.Apprise()
    if not sender.add(url):
        return False, "invalid_url"
    with apprise.LogCapture(level=logging.WARNING) as captured:
        ok = sender.notify(title=title, body=body, notify_type=notify_type)
        log = captured.getvalue()
    if ok:
        return True, None
    lines = [line.strip() for line in log.splitlines() if line.strip()]
    # "2026-11-18 18:50:37,743 - WARNING - A Connection error…" → the message only.
    last = re.sub(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d+ - [A-Z]+ - ", "", lines[-1]) if lines else "send_failed"
    return False, last[:500]


# PBO's icon for the channels that show one (ntfy, Discord), served by PUBLIC_URL.
ICON_PATH = "/favicon/web-app-manifest-192x192.png"
OPEN_LABEL = {"en": "Open", "fr": "Ouvrir"}


def send_to_channel(
    channel: NotificationChannel, *, title: str, body: str, level: str, link: str | None, event: str, language: str = "en"
) -> tuple[bool, str | None]:
    secrets = read_secrets(channel)
    try:
        url = apprise_url(
            channel,
            secrets,
            level=level,
            link=link,
            extras={"event": event, "level": level, "link": link or ""},
            icon=public_link(ICON_PATH),
            open_label=OPEN_LABEL.get(language, "Open"),
        )
    except (ChannelError, KeyError) as exc:
        return False, f"invalid_channel: {exc}"
    body_text = f"{body}\n{link}" if link and channel.kind not in ("ntfy", "webhook", "discord") else body
    try:
        ok, error = _apprise_notify(url, title, body_text, NOTIFY_TYPE["success" if event in ("run_success", "repair_completed") else level])
    except Exception as exc:  # noqa: BLE001 — report, never crash the worker
        ok, error = False, f"{exc.__class__.__name__}: {exc}"
    return ok, redact(error, secrets) if error else None


def send_test(db: Session, channel: NotificationChannel, *, record: bool = True) -> dict[str, Any]:
    """"Send a test": immediate, ignores events, level, quiet hours and dedup."""
    language = admin_language(db)
    title, body = catalog.TEST_MESSAGE[language]
    link = public_link("/notifications")
    ok, error = send_to_channel(channel, title=title, body=body, level="info", link=link, event="test", language=language)
    if record and channel.id is not None:
        now = _now()
        db.add(
            NotificationDelivery(
                channel_id=channel.id,
                channel_name=channel.name,
                event="test",
                level="info",
                dedup_key=f"test:{now.timestamp()}",
                title=title,
                body=body,
                link=link,
                status="sent" if ok else "failed",
                attempts=1,
                last_error=error,
                created_at=now,
                sent_at=now if ok else None,
            )
        )
        db.commit()
    return {"ok": ok, "error": error}


def _attempt(db: Session, delivery: NotificationDelivery, channel: NotificationChannel | None, now: datetime) -> None:
    if channel is None:
        delivery.status = "cancelled"
        delivery.next_attempt_at = None
        return
    # Rendered again: PBO's language may have changed since the event (B55).
    language = admin_language(db)
    delivery.title, delivery.body = render_delivery(delivery, language)
    ok, error = send_to_channel(
        channel, title=delivery.title, body=delivery.body, level=delivery.level, link=delivery.link, event=delivery.event, language=language
    )
    delivery.attempts += 1
    if ok:
        delivery.status = "sent"
        delivery.sent_at = now
        delivery.next_attempt_at = None
        delivery.last_error = None
        return
    delivery.last_error = error
    if delivery.attempts >= MAX_ATTEMPTS:
        delivery.status = "failed"
        delivery.next_attempt_at = None
    else:
        delivery.next_attempt_at = now + RETRY_DELAYS[delivery.attempts - 1]


def _send_digest(db: Session, channel: NotificationChannel, now: datetime) -> None:
    held = list(
        db.scalars(
            select(NotificationDelivery)
            .where(NotificationDelivery.channel_id == channel.id, NotificationDelivery.status == "digest")
            .order_by(NotificationDelivery.id)
        )
    )
    channel.last_digest_at = now
    if not held:
        return
    language = admin_language(db)
    title = catalog.render(catalog.DIGEST_TITLE[language], {"count": len(held)})
    for item in held:
        item.title, item.body = render_delivery(item, language)
    lines = [f"• {item.title} — {item.body}" + (f" (×{item.duplicates + 1})" if item.duplicates else "") for item in held]
    ok, error = send_to_channel(channel, title=title, body="\n".join(lines), level="info", link=public_link("/notifications"), event="digest", language=language)
    for item in held:
        item.attempts += 1
        item.status = "sent" if ok else "pending"
        item.sent_at = now if ok else None
        item.last_error = error
        # A failed summary falls back to sending the messages one by one (with retries).
        item.next_attempt_at = None if ok else now


def process_queue(db: Session, now: datetime | None = None) -> int:
    """Send what is due, the daily summaries, trim the journal. Returns messages handled."""
    now = now or _now()
    zone = _zone()
    channels = {channel.id: channel for channel in db.scalars(select(NotificationChannel))}
    due = list(
        db.scalars(
            select(NotificationDelivery)
            .where(NotificationDelivery.status == "pending", NotificationDelivery.next_attempt_at <= now)
            .order_by(NotificationDelivery.id)
            .limit(50)
        )
    )
    for delivery in due:
        channel = channels.get(delivery.channel_id)
        if channel is not None and not channel.enabled:
            continue  # a disabled channel keeps its queue until enabled again or deleted
        _attempt(db, delivery, channel, now)
        db.commit()
    for channel in channels.values():
        if channel.enabled and channel.digest and digest_due(channel, now, zone):
            _send_digest(db, channel, now)
            db.commit()
    db.execute(
        delete(NotificationDelivery)
        .where(NotificationDelivery.created_at < now - timedelta(days=JOURNAL_DAYS), NotificationDelivery.status.in_(("sent", "failed", "cancelled")))
        .execution_options(synchronize_session=False)
    )
    db.commit()
    return len(due)


def reschedule_channel(db: Session, channel: NotificationChannel, now: datetime | None = None) -> int:
    """B55: the channel's quiet hours or summary changed: what waits for it is
    placed again at once (sent now when nothing holds it any more). Messages
    waiting for a retry keep their delay. Returns the messages moved."""
    now = now or _now()
    zone = _zone()
    moved = 0
    waiting = db.scalars(
        select(NotificationDelivery).where(
            NotificationDelivery.channel_id == channel.id,
            NotificationDelivery.status.in_(("pending", "digest")),
            NotificationDelivery.attempts == 0,
        )
    )
    for delivery in waiting:
        status, at = "pending", now
        if delivery.level != "error":
            if channel.digest:
                status, at = "digest", None
            else:
                at = quiet_until(channel, now, zone) or now
        if (delivery.status, _aware(delivery.next_attempt_at)) != (status, at):
            delivery.status, delivery.next_attempt_at = status, at
            moved += 1
    db.commit()
    _wake.set()
    return moved


def cancel_channel_queue(db: Session, channel_id: int) -> None:
    for delivery in db.scalars(select(NotificationDelivery).where(NotificationDelivery.channel_id == channel_id, NotificationDelivery.status.in_(("pending", "digest")))):
        delivery.status = "cancelled"
        delivery.next_attempt_at = None


def delivery_view(delivery: NotificationDelivery, language: str | None = None) -> dict[str, Any]:
    title, body = render_delivery(delivery, language) if language else (delivery.title, delivery.body)
    return {
        "id": delivery.id,
        "channel_id": delivery.channel_id,
        "channel_name": delivery.channel_name,
        "event": delivery.event,
        "level": delivery.level,
        "title": title,
        "body": body,
        "link": delivery.link,
        "status": delivery.status,
        "attempts": delivery.attempts,
        "duplicates": delivery.duplicates,
        "next_attempt_at": delivery.next_attempt_at,
        "last_error": delivery.last_error,
        "created_at": delivery.created_at,
        "sent_at": delivery.sent_at,
    }


async def watch(session_factory: Callable[[], Session]) -> None:
    """Worker of the lifespan: every 15 s, or as soon as something is queued."""
    set_session_factory(session_factory)
    loop = asyncio.get_running_loop()
    while True:
        try:
            await loop.run_in_executor(None, _tick, session_factory)
        except Exception:
            logger.exception("Notification worker tick failed")
        await loop.run_in_executor(None, _wake.wait, WORKER_INTERVAL_SECONDS)
        _wake.clear()


def _tick(session_factory: Callable[[], Session]) -> None:
    with session_factory() as db:
        process_queue(db)
