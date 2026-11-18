"""History retention (Settings › Retention, Phase 5j).

Runs: keep all, delete those older than N days, or keep the N latest per disk.
Detailed journals (stdout/stderr of a run) can be cleared earlier, the run
itself (status, times, message) staying. Never touched:

* a pending or running run;
* the latest run and the latest successful backup of each disk (the disk
  sheet, the dashboard and the "N days without backup" reminder read them);
* every run of a disk with an open alert (verification recommended or
  required, damaged versions, pulled out while handed to PBS): they explain
  the alert.

`plan()` is pure over the database (the preview "X runs would be deleted");
`apply()` runs it, from the daily task.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import BackupRunStatus, ExternalBackupRun, ExternalDisk

MODES = ("keep_all", "days", "per_disk")
ACTIVE = {BackupRunStatus.PENDING, BackupRunStatus.RUNNING}


@dataclass(frozen=True)
class Policy:
    mode: str = "keep_all"
    days: int = 180
    keep_per_disk: int = 50
    log_detail_days: int = 0


@dataclass
class Plan:
    runs: list[int] = field(default_factory=list)
    logs: list[int] = field(default_factory=list)
    protected: int = 0


def policy_from_settings() -> Policy:
    settings = get_settings()
    return Policy(settings.run_history_mode, settings.run_history_days, settings.run_history_keep_per_disk, settings.run_log_detail_days)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _open_alert(disk: ExternalDisk) -> bool:
    return bool(disk.verify_recommended or disk.verify_required or disk.damaged_snapshots or disk.handoff_status == "pulled")


def plan(db: Session, policy: Policy, now: datetime | None = None) -> Plan:
    now = now or datetime.now(UTC)
    runs = list(db.scalars(select(ExternalBackupRun).order_by(ExternalBackupRun.started_at.desc(), ExternalBackupRun.id.desc())))
    alert_disks = {disk.id for disk in db.scalars(select(ExternalDisk)) if _open_alert(disk)}
    protected: set[int] = set()
    latest: set[int] = set()
    latest_backup: set[int] = set()
    for run in runs:
        if run.status in ACTIVE or run.disk_id in alert_disks:
            protected.add(run.id)
        if run.disk_id not in latest:
            latest.add(run.disk_id)
            protected.add(run.id)
        if run.disk_id not in latest_backup and run.status == BackupRunStatus.SUCCESS and not run.verify_only and not run.repair:
            latest_backup.add(run.disk_id)
            protected.add(run.id)

    result = Plan(protected=len(protected))
    if policy.mode == "days":
        cutoff = now - timedelta(days=policy.days)
        result.runs = [run.id for run in runs if run.id not in protected and _aware(run.started_at) < cutoff]
    elif policy.mode == "per_disk":
        seen: dict[int, int] = {}
        for run in runs:
            seen[run.disk_id] = seen.get(run.disk_id, 0) + 1
            if seen[run.disk_id] > policy.keep_per_disk and run.id not in protected:
                result.runs.append(run.id)
    if policy.log_detail_days > 0:
        cutoff = now - timedelta(days=policy.log_detail_days)
        gone = set(result.runs)
        result.logs = [
            run.id
            for run in runs
            if run.id not in protected and run.id not in gone and _aware(run.started_at) < cutoff and (run.stdout_log or run.stderr_log)
        ]
    return result


def apply(db: Session, policy: Policy | None = None, now: datetime | None = None) -> Plan:
    from app.services.external_backups import _detach_planned_runs

    result = plan(db, policy or policy_from_settings(), now)
    if result.runs:
        _detach_planned_runs(db, result.runs)
        for run in db.scalars(select(ExternalBackupRun).where(ExternalBackupRun.id.in_(result.runs))):
            db.delete(run)
    if result.logs:
        for run in db.scalars(select(ExternalBackupRun).where(ExternalBackupRun.id.in_(result.logs))):
            run.stdout_log = None
            run.stderr_log = None
    db.commit()
    return result
