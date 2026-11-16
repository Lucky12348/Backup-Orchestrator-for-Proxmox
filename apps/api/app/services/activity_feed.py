"""Activity page (Phase 6): one chronological list, newest first.

* `backup` / `eject`: external backup runs (a backup with auto-eject is ONE
  backup item, B24), with their duration and a one-line summary (transferred
  size, "no new data", failure code/message, progress);
* `detection`: USB disks plugged / unplugged (hot-plug history);
* `planning`: planned runs that ended without a backup (missed, cancelled) or
  wait for a confirmation.

Texts are built by the UI from codes and values (FR/EN); `message` is the raw
text kept for the details and the search.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ExternalBackupRun, ExternalDisk, ScheduledBackupEvent, ScheduledBackupRun, ScheduledBackupRunStatus
from app.models.activity_event import ActivityEvent
from app.services.disk_identity import disk_matches_serial
from app.services.run_log import run_kind, transfer_summary

MAX_ITEMS = 1000
_PLANNING_SHOWN = {
    ScheduledBackupRunStatus.MISSED: "missed",
    ScheduledBackupRunStatus.CANCELLED: "cancelled",
    ScheduledBackupRunStatus.WAITING_FOR_CONFIRMATION: "to_confirm",
}


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() + "Z" if value else None


def _status(value: Any) -> str:
    return str(getattr(value, "value", value))


def _disk_for_serial(disks: list[ExternalDisk], serial: str | None) -> ExternalDisk | None:
    if not serial:
        return None
    return next((disk for disk in disks if disk_matches_serial(disk, serial)), None)


def build_activity(db: Session, *, days: int = 90, now: datetime | None = None) -> list[dict[str, Any]]:
    now = now or datetime.utcnow()
    since = now - timedelta(days=max(1, min(days, 366)))
    disks = list(db.scalars(select(ExternalDisk)))
    by_id = {disk.id: disk for disk in disks}
    items: list[dict[str, Any]] = []

    planned_backup_ids = {
        run_id for run_id in db.scalars(select(ScheduledBackupRun.backup_run_id).where(ScheduledBackupRun.backup_run_id.is_not(None)))
    }
    for run in db.scalars(select(ExternalBackupRun).where(ExternalBackupRun.started_at >= since)):
        disk = by_id.get(run.disk_id)
        kind = run_kind(run, disk)
        summary = transfer_summary(run.stdout_log) if kind == "backup" and _status(run.status) == "success" else None
        end = run.finished_at
        items.append(
            {
                "id": f"run-{run.id}",
                "type": kind,
                "status": _status(run.status),
                "at": _iso(run.started_at),
                "finished_at": _iso(end),
                "duration_seconds": int((end - run.started_at).total_seconds()) if end else None,
                "disk_id": run.disk_id,
                "disk_label": (disk.display_name if disk else None) or f"#{run.disk_id}",
                "run_id": run.id,
                "planned": run.id in planned_backup_ids,
                "auto_eject": bool(run.auto_eject_after_success),
                "step": run.current_step,
                "progress_percent": run.progress_percent,
                "transferred_bytes": (summary or {}).get("transferred_bytes"),
                "no_new_data": bool((summary or {}).get("no_new_data")),
                "message": run.message,
            }
        )

    for event in db.scalars(
        select(ActivityEvent).where(ActivityEvent.category == "disk", ActivityEvent.created_at >= since)
    ):
        details = event.details or {}
        disk = _disk_for_serial(disks, details.get("serial"))
        items.append(
            {
                "id": f"event-{event.id}",
                "type": "detection",
                "status": "plugged" if event.action == "usb_added" else "unplugged",
                "at": _iso(event.created_at),
                "finished_at": None,
                "duration_seconds": None,
                "disk_id": disk.id if disk else None,
                "disk_label": (disk.display_name if disk else None) or details.get("model") or details.get("serial") or "?",
                "run_id": None,
                "planned": False,
                "auto_eject": False,
                "step": None,
                "progress_percent": None,
                "transferred_bytes": None,
                "no_new_data": False,
                "message": event.summary,
            }
        )

    for run, event in db.execute(
        select(ScheduledBackupRun, ScheduledBackupEvent)
        .join(ScheduledBackupEvent, ScheduledBackupEvent.id == ScheduledBackupRun.event_id)
        .where(ScheduledBackupRun.scheduled_for >= since, ScheduledBackupRun.scheduled_for <= now)
    ):
        shown = _PLANNING_SHOWN.get(run.status)
        if shown is None or run.backup_run_id is not None:
            continue
        serial = run.disk_serial or event.disk_serial
        disk = _disk_for_serial(disks, serial)
        items.append(
            {
                "id": f"planning-{run.id}",
                "type": "planning",
                "status": shown,
                "at": _iso(run.scheduled_for),
                "finished_at": _iso(run.finished_at),
                "duration_seconds": None,
                "disk_id": disk.id if disk else None,
                "disk_label": (disk.display_name if disk else None) or event.disk_label_or_model or serial,
                "run_id": None,
                "planning_run_id": run.id,
                "event_title": event.title,
                "planned": True,
                "auto_eject": False,
                "step": None,
                "progress_percent": None,
                "transferred_bytes": None,
                "no_new_data": False,
                "message": run.error,
            }
        )

    items.sort(key=lambda item: item["at"] or "", reverse=True)
    return items[:MAX_ITEMS]
