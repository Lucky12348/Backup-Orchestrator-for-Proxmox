"""Safety guards around rotation disks (Phase 5f).

`disk_alerts` is what the UI shows on disk cards and in the dashboard
"To do" (decided here, displayed there):

  usage_critical / usage_warning   datastore ≥ 90 % / ≥ 80 % used
  smart_danger / smart_warning     SMART failing / reallocated sectors, errors
  verify_recommended               unplugged without eject (mounted or during a run)
  usb2_slow                        USB 2.0 link (≈ 10× slower)
  rotation_stale                   approved disk without a backup for N days

Unsafe removal: a disk handed to the PBS VM is invisible to the host; seeing
it again on the host means it was pulled out without eject. Unplugged during
a run (before the hand-off) counts too. The disk then gets
`verify_recommended`: a PBS verify is proposed when it is plugged back.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ExternalBackupRun, ExternalDisk
from app.models.backup_run import BackupRunStatus
from app.services import disk_profile

ACTIVE = (BackupRunStatus.PENDING, BackupRunStatus.RUNNING)


def _alert(disk: ExternalDisk, code: str, level: str, **params: Any) -> dict[str, Any]:
    return {"disk_id": disk.id, "disk_name": disk.display_name, "code": code, "level": level, "params": params}


def last_backup_at(db: Session) -> dict[int, datetime]:
    """Last successful backup (not a manual eject, not a verify-only run) per disk."""
    from app.services.run_log import run_kind

    disks = {disk.id: disk for disk in db.scalars(select(ExternalDisk))}
    result: dict[int, datetime] = {}
    for run in db.scalars(
        select(ExternalBackupRun).where(
            ExternalBackupRun.status == BackupRunStatus.SUCCESS, ExternalBackupRun.finished_at.is_not(None)
        )
    ):
        if run.verify_only or run_kind(run, disks.get(run.disk_id)) != "backup":
            continue
        if run.finished_at > result.get(run.disk_id, datetime.min):
            result[run.disk_id] = run.finished_at
    return result


def disk_alerts(db: Session, *, now: datetime | None = None, stale_days: int | None = None) -> list[dict[str, Any]]:
    from app.services.settings_service import effective_settings

    moment = now or datetime.utcnow()
    days = stale_days if stale_days is not None else effective_settings(db).rotation_stale_days
    backups = last_backup_at(db)
    alerts: list[dict[str, Any]] = []
    disks = db.scalars(
        select(ExternalDisk).where(ExternalDisk.serial_number.not_like("agent-report::%")).order_by(ExternalDisk.display_name)
    )
    for disk in disks:
        if disk.operator_decision == "rejected":
            continue
        percent = disk_profile.usage_percent(disk.filesystem_used_gb, disk.filesystem_total_gb)
        level = disk_profile.usage_level(percent)
        if level is not None and disk.prepared_as_pbs_datastore:
            alerts.append(
                _alert(
                    disk,
                    "usage_critical" if level >= disk_profile.USAGE_CRITICAL_PERCENT else "usage_warning",
                    "danger" if level >= disk_profile.USAGE_CRITICAL_PERCENT else "warning",
                    percent=percent,
                    used_gb=disk.filesystem_used_gb,
                    total_gb=disk.filesystem_total_gb,
                )
            )
        if disk.smart_status == "danger":
            alerts.append(_alert(disk, "smart_danger", "danger", **_smart_params(disk)))
        elif disk.smart_status == "warning":
            alerts.append(_alert(disk, "smart_warning", "warning", **_smart_params(disk)))
        if disk.verify_recommended:
            alerts.append(
                _alert(
                    disk,
                    "verify_recommended",
                    "warning",
                    removed_at=disk.unsafe_removal_at.isoformat(timespec="seconds") + "Z" if disk.unsafe_removal_at else None,
                    connected=bool(disk.connected),
                )
            )
        if disk_profile.is_usb2(disk) and disk.connected:
            alerts.append(_alert(disk, "usb2_slow", "warning", speed_mbps=disk.usb_speed_mbps))
        last = backups.get(disk.id)
        if disk.trusted and last is not None and moment - last >= timedelta(days=days):
            alerts.append(_alert(disk, "rotation_stale", "warning", days=(moment - last).days, last_backup_at=last.isoformat(timespec="seconds") + "Z", threshold_days=days))
    return alerts


def _smart_params(disk: ExternalDisk) -> dict[str, Any]:
    details = disk.smart_details if isinstance(disk.smart_details, dict) else {}
    return {key: details.get(key) for key in ("reasons", "reallocated", "pending", "uncorrectable", "media_errors") if details.get(key) is not None}


# --- unsafe removal ------------------------------------------------------------------------------
def active_run_for(db: Session, disk_id: int) -> ExternalBackupRun | None:
    return db.scalar(
        select(ExternalBackupRun).where(ExternalBackupRun.disk_id == disk_id, ExternalBackupRun.status.in_(ACTIVE)).limit(1)
    )


def mark_unsafe_removal(db: Session, disk: ExternalDisk, *, reason: str, now: datetime | None = None) -> bool:
    """Flags the disk once (until a verify clears it): activity line + notification."""
    if disk.verify_recommended:
        return False
    moment = now or datetime.utcnow()
    disk.verify_recommended = True
    disk.unsafe_removal_at = moment
    db.add(disk)
    from app.models import ActivityEvent

    db.add(
        ActivityEvent(
            created_at=moment,
            category="disk",
            action="unsafe_removal",
            status="warning",
            summary=f"{disk.display_name} ({disk.serial_number}) unplugged without eject ({reason}): PBS verify recommended.",
            details={"serial": disk.serial_number, "disk_id": disk.id, "reason": reason},
        )
    )
    from app.services.notifications import notify_unsafe_removal

    notify_unsafe_removal(disk.display_name or disk.serial_number)
    return True


def clear_verify_recommended(disk: ExternalDisk) -> None:
    disk.verify_recommended = None
    disk.unsafe_removal_at = None
