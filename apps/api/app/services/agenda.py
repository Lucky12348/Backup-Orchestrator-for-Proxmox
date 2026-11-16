"""Calendar items of the planning page (Phase 6, docs/design/mockups/planning.html).

One list for a visible range, whatever their origin:

* `external` / `monthly`: occurrences of the planned backups (monthly
  recurrence = the "monthly sync" calendar), with the state of their planned
  run and of the backup run it started;
* `manual`: external backups started by hand (not by a planning), ejects
  excluded;
* `proxmox`: Proxmox VE backup jobs (read only), from their schedule
  (services/pve_schedule.py), when asked for.

Status: planned | waiting_disk | to_confirm | waiting | running | success |
failed | missed | cancelled.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ExternalBackupRun, ExternalDisk, ScheduledBackupEvent, ScheduledBackupRun, ScheduledBackupRunStatus
from app.models.backup_run import BackupRunStatus
from app.services import pve_schedule, recurrence
from app.services.disk_identity import disk_matches_serial
from app.services.run_log import run_kind

logger = logging.getLogger(__name__)

PLANNED_DISPLAY_MINUTES = 60
PROXMOX_DISPLAY_MINUTES = 45
PROXMOX_CACHE_SECONDS = 60.0

_RUN_STATUS = {
    ScheduledBackupRunStatus.PENDING: "planned",
    ScheduledBackupRunStatus.WAITING_FOR_DISK: "waiting_disk",
    ScheduledBackupRunStatus.WAITING_FOR_CONFIRMATION: "to_confirm",
    ScheduledBackupRunStatus.WAITING_FOR_EXTERNAL_BACKUP: "waiting",
    ScheduledBackupRunStatus.RUNNING: "running",
    ScheduledBackupRunStatus.SUCCESS: "success",
    ScheduledBackupRunStatus.FAILURE: "failed",
    ScheduledBackupRunStatus.MISSED: "missed",
    ScheduledBackupRunStatus.CANCELLED: "cancelled",
}
_BACKUP_STATUS = {
    BackupRunStatus.PENDING: "running",
    BackupRunStatus.RUNNING: "running",
    BackupRunStatus.SUCCESS: "success",
    BackupRunStatus.FAILED: "failed",
}

_cache_lock = threading.Lock()
_proxmox_cache: tuple[float, list[dict[str, Any]] | None, str | None] | None = None


def reset_proxmox_cache() -> None:
    global _proxmox_cache
    with _cache_lock:
        _proxmox_cache = None


def _proxmox_jobs() -> tuple[list[dict[str, Any]], str | None]:
    """PVE backup jobs, cached briefly (the calendar asks on every navigation)."""
    global _proxmox_cache
    with _cache_lock:
        cached = _proxmox_cache
    if cached and time.monotonic() - cached[0] < PROXMOX_CACHE_SECONDS:
        return cached[1] or [], cached[2]
    try:
        from app.core.config import get_settings
        from app.services.proxmox_client import ProxmoxClient

        jobs, error = ProxmoxClient(get_settings()).list_backup_jobs(), None
    except Exception as exc:  # noqa: BLE001 - shown as "Proxmox jobs unavailable"
        jobs, error = [], str(exc)
    with _cache_lock:
        _proxmox_cache = (time.monotonic(), jobs, error)
    return jobs, error


def _disk_label(disks: list[ExternalDisk], serial: str, fallback: str | None) -> str:
    for disk in disks:
        if disk_matches_serial(disk, serial):
            return disk.display_name or serial
    return fallback or serial


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() + "Z" if value else None


def planned_items(db: Session, start: datetime, end: datetime, now: datetime, disks: list[ExternalDisk]) -> tuple[list[dict], set[int]]:
    events = list(
        db.scalars(
            select(ScheduledBackupEvent).where(ScheduledBackupEvent.deleted_at.is_(None)).order_by(ScheduledBackupEvent.id)
        )
    )
    runs = list(
        db.scalars(
            select(ScheduledBackupRun).where(
                ScheduledBackupRun.scheduled_for >= start - timedelta(days=1),
                ScheduledBackupRun.scheduled_for <= end,
            )
        )
    )
    by_key = {(run.event_id, run.scheduled_for): run for run in runs}
    linked = {run.backup_run_id for run in db.scalars(select(ScheduledBackupRun).where(ScheduledBackupRun.backup_run_id.is_not(None)))}
    from app.services.datastore_health import read_health

    health = read_health(db) or {}
    blocked_events = set(health.get("blocked_events") or []) if health.get("checked") else set()
    items: list[dict] = []
    for event in events:
        kind = "monthly" if getattr(event.recurrence_type, "value", event.recurrence_type) == "monthly" else "external"
        occurrences = set(recurrence.expand(event, start, end)) if event.enabled else set()
        # "Run now" runs are not occurrences: show them where they happened.
        extra = [run.scheduled_for for run in runs if run.event_id == event.id and start <= run.scheduled_for <= end]
        for scheduled_for in sorted(occurrences | set(extra)):
            run = by_key.get((event.id, scheduled_for))
            backup = db.get(ExternalBackupRun, run.backup_run_id) if run and run.backup_run_id else None
            if backup is not None:
                status = _BACKUP_STATUS.get(backup.status, "running")
                starts_at = backup.started_at
                ends_at = backup.finished_at or max(now, backup.started_at + timedelta(minutes=15))
            else:
                if run is not None:
                    status = _RUN_STATUS.get(run.status, "planned")
                    if status == "planned" and scheduled_for <= now:
                        status = "waiting_disk"
                else:
                    status = "planned" if scheduled_for > now else ("missed" if scheduled_for + timedelta(minutes=event.window_duration_minutes) < now else "waiting_disk")
                starts_at = scheduled_for
                ends_at = scheduled_for + timedelta(minutes=PLANNED_DISPLAY_MINUTES)
            if event.id in blocked_events and status in {"planned", "waiting_disk", "to_confirm", "waiting"}:
                status = "blocked"  # its source datastore no longer exists (Phase 6c)
            serial = (run.disk_serial if run and run.disk_serial else recurrence.disk_serial_for(event, scheduled_for))
            items.append(
                {
                    "id": f"plan-{event.id}-{scheduled_for.isoformat()}",
                    "kind": kind,
                    "title": event.title,
                    "starts_at": _iso(starts_at),
                    "ends_at": _iso(ends_at),
                    "scheduled_for": _iso(scheduled_for),
                    "window_ends_at": _iso(scheduled_for + timedelta(minutes=event.window_duration_minutes)),
                    "status": status,
                    "event_id": event.id,
                    "planning_run_id": run.id if run else None,
                    "backup_run_id": backup.id if backup else None,
                    "disk_serial": serial,
                    "disk_label": _disk_label(disks, serial, event.disk_label_or_model if serial == event.disk_serial else None),
                    "progress_percent": backup.progress_percent if backup else None,
                    "message": (backup.message if backup else (run.error if run else None)),
                    "read_only": False,
                }
            )
    return items, {item for item in linked if item is not None}


def manual_items(db: Session, start: datetime, end: datetime, now: datetime, linked: set[int]) -> list[dict]:
    rows = db.execute(
        select(ExternalBackupRun, ExternalDisk)
        .join(ExternalDisk, ExternalDisk.id == ExternalBackupRun.disk_id)
        .where(ExternalBackupRun.started_at <= end)
        .order_by(ExternalBackupRun.started_at)
    )
    items: list[dict] = []
    for run, disk in rows:
        finished = run.finished_at or now
        if finished < start or run.id in linked or run_kind(run, disk) == "eject":
            continue
        items.append(
            {
                "id": f"run-{run.id}",
                "kind": "manual",
                "title": None,
                "starts_at": _iso(run.started_at),
                "ends_at": _iso(max(finished, run.started_at + timedelta(minutes=15))),
                "scheduled_for": None,
                "window_ends_at": None,
                "status": _BACKUP_STATUS.get(run.status, "running"),
                "event_id": None,
                "planning_run_id": None,
                "backup_run_id": run.id,
                "disk_serial": disk.serial_number,
                "disk_label": disk.display_name or disk.serial_number,
                "progress_percent": run.progress_percent,
                "message": run.message,
                "read_only": False,
            }
        )
    return items


def proxmox_items(start: datetime, end: datetime, zone_name: str | None) -> tuple[list[dict], list[str], str | None]:
    jobs, error = _proxmox_jobs()
    items: list[dict] = []
    unsupported: list[str] = []
    for job in jobs:
        job_id = str(job.get("id") or "")
        if not job_id or str(job.get("enabled", 1)) in {"0", "False", "false"}:
            continue
        schedule = pve_schedule.parse(job.get("schedule"))
        if schedule is None:
            unsupported.append(job_id)
            continue
        storage = str(job.get("storage") or "")
        for moment in pve_schedule.occurrences(schedule, start, end, zone_name):
            items.append(
                {
                    "id": f"pve-{job_id}-{moment.isoformat()}",
                    "kind": "proxmox",
                    # Readable name built by the UI ("Proxmox backup → <storage>");
                    # the job id only in the details.
                    "title": None,
                    "job_id": job_id,
                    "job_comment": job.get("comment") or None,
                    "starts_at": _iso(moment),
                    "ends_at": _iso(moment + timedelta(minutes=PROXMOX_DISPLAY_MINUTES)),
                    "scheduled_for": _iso(moment),
                    "window_ends_at": None,
                    "status": "planned",
                    "event_id": None,
                    "planning_run_id": None,
                    "backup_run_id": None,
                    "disk_serial": None,
                    "disk_label": storage,
                    "progress_percent": None,
                    "message": str(job.get("schedule") or ""),
                    "read_only": True,
                }
            )
    return items, unsupported, error


def build_agenda(
    db: Session, start: datetime, end: datetime, *, include_proxmox: bool, zone_name: str | None, now: datetime | None = None
) -> dict[str, Any]:
    now = now or datetime.utcnow()
    disks = list(db.scalars(select(ExternalDisk)))
    items, linked = planned_items(db, start, end, now, disks)
    items += manual_items(db, start, end, now, linked)
    unsupported: list[str] = []
    error = None
    if include_proxmox:
        pve, unsupported, error = proxmox_items(start, end, zone_name)
        items += pve
    items.sort(key=lambda item: item["starts_at"] or "")
    return {"items": items, "timezone": zone_name, "proxmox_unsupported": unsupported, "proxmox_error": error}
