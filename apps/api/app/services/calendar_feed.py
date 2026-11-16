"""iCalendar feed of the backup planning (`GET /calendar.ics?token=…`).

Calendar clients cannot send a session cookie, so the feed is protected by a
dedicated random token (stored encrypted in `app_settings`, key
`meta.calendar_token`), regenerated from the UI. No token = feed disabled.

* Enabled, not deleted planning events → one VEVENT each. Recurring ones carry
  an RRULE matching `services/recurrence.py`: the occurrences repeat the LOCAL
  time of the planning's zone, so DTSTART is `TZID=<IANA zone>` local time
  (one-off events stay in UTC). `INTERVAL` = every N days/weeks/months.
  Monthly events on day 29-31 fall back to the last day of shorter months,
  which `BYMONTHDAY=28,…,d;BYSETPOS=-1` expresses exactly.
* `history=true` adds the external backup runs of the last `history_days`.
* `proxmox=true` adds the Proxmox VE backup jobs whose schedule is understood
  (services/pve_schedule.py), read only, in the app time zone.
"""

from __future__ import annotations

import secrets
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import SecretDecryptionError, decrypt_value, encrypt_value
from app.models.app_setting import AppSetting
from app.models.external_backup_run import ExternalBackupRun
from app.models.external_disk import ExternalDisk
from app.models.scheduled_backup import ScheduledBackupEvent, ScheduledBackupRecurrenceType
from app.services import pve_schedule, recurrence

CALENDAR_TOKEN_KEY = "meta.calendar_token"
PRODID = "-//Proxmox Backup Orchestrator//Planning//EN"
MAX_HISTORY_DAYS = 366


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --- Token -------------------------------------------------------------------------


def regenerate_calendar_token(db: Session) -> str:
    token = secrets.token_urlsafe(32)
    row = db.get(AppSetting, CALENDAR_TOKEN_KEY)
    if row is None:
        db.add(AppSetting(key=CALENDAR_TOKEN_KEY, value=encrypt_value(token), is_secret=True))
    else:
        row.value = encrypt_value(token)
        row.is_secret = True
    db.commit()
    return token


def current_calendar_token(db: Session) -> str | None:
    row = db.get(AppSetting, CALENDAR_TOKEN_KEY)
    if row is None or not row.value:
        return None
    try:
        return decrypt_value(row.value)
    except SecretDecryptionError:
        return None


def disable_calendar_token(db: Session) -> None:
    row = db.get(AppSetting, CALENDAR_TOKEN_KEY)
    if row is not None:
        db.delete(row)
        db.commit()


def check_calendar_token(db: Session, token: str | None) -> bool:
    expected = current_calendar_token(db)
    return bool(token and expected and secrets.compare_digest(token, expected))


# --- iCalendar rendering -------------------------------------------------------------


def _escape(text: str) -> str:
    return (
        text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r\n", "\\n").replace("\n", "\\n")
    )


def _utc(value: datetime) -> str:
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value.strftime("%Y%m%dT%H%M%SZ")


def _fold(line: str) -> str:
    """RFC 5545 §3.1: lines longer than 75 octets are folded."""
    encoded = line.encode("utf-8")
    if len(encoded) <= 75:
        return line
    parts: list[str] = []
    current = b""
    limit = 75
    for char in line:
        piece = char.encode("utf-8")
        if len(current) + len(piece) > limit:
            parts.append(current.decode("utf-8"))
            current = b""
            limit = 74  # continuation lines start with a space
        current += piece
    parts.append(current.decode("utf-8"))
    return "\r\n ".join(parts)


def _local_start(event: ScheduledBackupEvent) -> datetime:
    return recurrence.to_local(event.window_starts_at, recurrence._zone(getattr(event, "timezone", None)))


def recurrence_rule(event: ScheduledBackupEvent) -> str | None:
    kind = event.recurrence_type
    interval = recurrence.interval_of(event)
    suffix = f";INTERVAL={interval}" if interval > 1 else ""
    if kind == ScheduledBackupRecurrenceType.DAILY:
        return f"FREQ=DAILY{suffix}"
    if kind == ScheduledBackupRecurrenceType.WEEKLY:
        days = recurrence.weekdays_of(event)
        if days:
            return f"FREQ=WEEKLY{suffix};BYDAY={','.join(['MO', 'TU', 'WE', 'TH', 'FR', 'SA', 'SU'][day] for day in days)}"
        return f"FREQ=WEEKLY{suffix}"
    if kind == ScheduledBackupRecurrenceType.MONTHLY:
        day = _local_start(event).day
        if day <= 28:
            return f"FREQ=MONTHLY{suffix};BYMONTHDAY={day}"
        return f"FREQ=MONTHLY{suffix};BYMONTHDAY={','.join(str(d) for d in range(28, day + 1))};BYSETPOS=-1"
    return None


def _local(value: datetime) -> str:
    return value.strftime("%Y%m%dT%H%M%S")


def _event_lines(event: ScheduledBackupEvent, stamp: str) -> list[str]:
    start = event.window_starts_at
    end = start + timedelta(minutes=event.window_duration_minutes)
    description = [
        f"Disque : {event.disk_label_or_model or event.disk_serial}",
        f"Datastore source : {event.datastore}",
        f"Démarrage : {'automatique à la détection du disque' if event.start_mode.value == 'auto_on_disk_detected' else 'confirmation manuelle'}",
    ]
    if event.auto_eject_after_success:
        description.append("Éjection automatique après succès")
    rule = recurrence_rule(event)
    zone = recurrence._zone(getattr(event, "timezone", None)).key
    if rule:
        local_start = _local_start(event)
        local_end = local_start + timedelta(minutes=event.window_duration_minutes)
        when = [f"DTSTART;TZID={zone}:{_local(local_start)}", f"DTEND;TZID={zone}:{_local(local_end)}"]
    else:
        when = [f"DTSTART:{_utc(start)}", f"DTEND:{_utc(end)}"]
    serials = recurrence.rotation_serials(event)
    if len(serials) > 1:
        description[0] = f"Disques (rotation) : {' → '.join(serials)}"
    lines = [
        "BEGIN:VEVENT",
        f"UID:pbo-planning-{event.id}@proxmox-backup-orchestrator",
        f"DTSTAMP:{stamp}",
        *when,
        f"SUMMARY:{_escape(event.title)}",
        f"DESCRIPTION:{_escape(chr(10).join(description))}",
        "CATEGORIES:Sauvegarde",
        "TRANSP:TRANSPARENT",
    ]
    if rule:
        lines.append(f"RRULE:{rule}")
    lines.append("END:VEVENT")
    return lines


def _run_lines(run: ExternalBackupRun, disk_name: str, stamp: str) -> list[str]:
    end = run.finished_at or run.started_at + timedelta(minutes=1)
    if end <= run.started_at:
        end = run.started_at + timedelta(minutes=1)
    status = run.status.value if hasattr(run.status, "value") else str(run.status)
    lines = [
        "BEGIN:VEVENT",
        f"UID:pbo-run-{run.id}@proxmox-backup-orchestrator",
        f"DTSTAMP:{stamp}",
        f"DTSTART:{_utc(run.started_at)}",
        f"DTEND:{_utc(end)}",
        f"SUMMARY:{_escape(f'Sauvegarde externe {disk_name} : {status}')}",
        f"DESCRIPTION:{_escape(run.message or '')}",
        "CATEGORIES:Historique",
        "TRANSP:TRANSPARENT",
        "END:VEVENT",
    ]
    return lines


def _history_runs(db: Session, since: datetime) -> Iterable[tuple[ExternalBackupRun, str]]:
    rows = db.execute(
        select(ExternalBackupRun, ExternalDisk)
        .join(ExternalDisk, ExternalDisk.id == ExternalBackupRun.disk_id)
        .where(ExternalBackupRun.started_at >= since)
        .order_by(ExternalBackupRun.started_at)
    )
    for run, disk in rows:
        yield run, disk.display_name or disk.serial_number


def _proxmox_lines(stamp: str, zone_name: str, now: datetime) -> list[str]:
    from app.services.agenda import _proxmox_jobs

    jobs, _error = _proxmox_jobs()
    zone = recurrence._zone(zone_name)
    today = recurrence.to_local(now, zone).date()
    lines: list[str] = []
    for job in jobs:
        job_id = str(job.get("id") or "")
        schedule = pve_schedule.parse(job.get("schedule"))
        if not job_id or schedule is None or str(job.get("enabled", 1)) in {"0", "False", "false"}:
            continue
        hour, minute = schedule.times[0]
        start = datetime(today.year, today.month, today.day, hour, minute)
        lines += [
            "BEGIN:VEVENT",
            f"UID:pbo-pve-{_escape(job_id)}@proxmox-backup-orchestrator",
            f"DTSTAMP:{stamp}",
            f"DTSTART;TZID={zone.key}:{_local(start)}",
            f"DTEND;TZID={zone.key}:{_local(start + timedelta(minutes=45))}",
            f"RRULE:{schedule.rrule()}",
            f"SUMMARY:{_escape('Sauvegarde Proxmox → ' + str(job.get('storage') or job_id))}",
            f"DESCRIPTION:{_escape(f'Job Proxmox VE {job_id} (lecture seule) · ' + str(job.get('schedule') or ''))}",
            "CATEGORIES:Proxmox",
            "TRANSP:TRANSPARENT",
            "END:VEVENT",
        ]
    return lines


def build_calendar(
    db: Session,
    *,
    include_history: bool = False,
    history_days: int = 90,
    include_proxmox: bool = False,
    zone_name: str | None = None,
    now: datetime | None = None,
) -> str:
    now = now or _utcnow()
    stamp = _utc(now)
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{PRODID}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:Sauvegardes externes",
    ]
    events = db.scalars(
        select(ScheduledBackupEvent)
        .where(ScheduledBackupEvent.deleted_at.is_(None), ScheduledBackupEvent.enabled.is_(True))
        .order_by(ScheduledBackupEvent.id)
    )
    for event in events:
        lines.extend(_event_lines(event, stamp))
    if include_history:
        days = max(1, min(history_days, MAX_HISTORY_DAYS))
        since = (now.astimezone(timezone.utc) if now.tzinfo else now).replace(tzinfo=None) - timedelta(days=days)
        for run, disk_name in _history_runs(db, since):
            lines.extend(_run_lines(run, disk_name, stamp))
    if include_proxmox:
        naive_now = (now.astimezone(timezone.utc) if now.tzinfo else now).replace(tzinfo=None)
        lines.extend(_proxmox_lines(stamp, zone_name or "UTC", naive_now))
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"
