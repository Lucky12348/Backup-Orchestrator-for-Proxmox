from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class NotificationChannel(Base):
    """One place PBO sends notifications to (services/notification_channels.py).

    kind: ntfy | gotify | discord | telegram | email | webhook | apprise.
    `config` holds the non-secret fields; `secrets_encrypted` the secret ones
    (Fernet JSON, like the secret app_settings). `events` = event keys of
    services/notification_catalog.py; `min_level` = info | warning | error.
    Quiet hours ("HH:MM", PBO time zone) hold everything but errors; `digest`
    groups non-error messages in one daily summary at `digest_time`.

    Schema owned by Alembic (0015_notification_channels).
    """

    __tablename__ = "notification_channels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    secrets_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    events: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    min_level: Mapped[str] = mapped_column(String(8), nullable=False, default="info")
    quiet_start: Mapped[str | None] = mapped_column(String(5), nullable=True)
    quiet_end: Mapped[str | None] = mapped_column(String(5), nullable=True)
    digest: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    digest_time: Mapped[str] = mapped_column(String(5), nullable=False, default="08:00")
    last_digest_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow)


class NotificationDelivery(Base):
    """One message for one channel: the queue AND the send journal.

    status: pending (waiting to be sent, `next_attempt_at`) | digest (held for
    the daily summary) | sent | failed (gave up, `last_error`) | cancelled
    (channel deleted). `duplicates` counts identical alerts folded into this
    one (same `dedup_key` within the event's window).

    Schema owned by Alembic (0015_notification_channels).
    """

    __tablename__ = "notification_deliveries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    channel_name: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    event: Mapped[str] = mapped_column(String(48), nullable=False)
    level: Mapped[str] = mapped_column(String(8), nullable=False)
    dedup_key: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    link: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duplicates: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
