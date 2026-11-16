import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePosixPath
from threading import Lock
from typing import Literal

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models import BackupRunStatus, ExternalBackupMode, ExternalBackupRun, ExternalDisk
from app.services.disk_handoff import handoff_disk_to_pbs
from app.services.external_backup_agent import AgentCommandError, AgentCompatibilityError
from app.services.external_backup_agent import get_external_backup_agent_bridge
from app.services.external_backup_execution import build_export_target_path, get_external_backup_execution_service
from app.services.notifications import notify_backup_failure, notify_backup_success
from app.services.run_log import is_progress_line
from app.services.pbs_progress import parse_pbs_progress_line, summarize_pbs_failure


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExternalBackupPlan:
    target_path: str
    mode: ExternalBackupMode
    preserves_existing_data: bool


def _expected_pbs_mount_path(serial_number: str) -> PurePosixPath:
    return PurePosixPath("/mnt/pbo") / serial_number


def build_external_backup_plan(disk: ExternalDisk) -> ExternalBackupPlan:
    base_path = PurePosixPath(disk.pbs_mount_path).parent if disk.pbs_mount_path else _expected_pbs_mount_path(disk.serial_number)
    settings = get_settings()

    if disk.dedicated_backup_disk or not settings.external_backup_legacy_coexistence_enabled:
        return ExternalBackupPlan(
            target_path=build_export_target_path(str(base_path), disk.serial_number, ExternalBackupMode.DEDICATED),
            mode=ExternalBackupMode.DEDICATED,
            preserves_existing_data=False,
        )

    if settings.external_backup_legacy_coexistence_enabled and disk.allow_existing_data:
        return ExternalBackupPlan(
            target_path=build_export_target_path(str(base_path), disk.serial_number, ExternalBackupMode.COEXISTENCE),
            mode=ExternalBackupMode.COEXISTENCE,
            preserves_existing_data=True,
        )

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Disk must be dedicated before an external backup can run.",
    )


def _get_disk_or_404(db: Session, disk_id: int) -> ExternalDisk:
    disk = db.get(ExternalDisk, disk_id)
    if disk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Disk not found")
    return disk


def get_external_backup_preview(db: Session, disk_id: int) -> dict[str, str | bool | int | None]:
    disk = _get_disk_or_404(db, disk_id)
    plan = build_external_backup_plan(disk)
    settings = get_settings()
    estimated_needed_gb = disk.usable_capacity_gb or disk.capacity_gb
    loop_size_gb = settings.external_loop_datastore_size_gb if plan.mode == ExternalBackupMode.COEXISTENCE else None
    return {
        "target_path": plan.target_path,
        "mode": plan.mode.value,
        "preserves_existing_data": plan.preserves_existing_data,
        "loop_image_size_gb": loop_size_gb,
        "loop_image_size_warning": bool(loop_size_gb is not None and estimated_needed_gb and loop_size_gb < estimated_needed_gb),
    }


def list_external_backup_runs(db: Session) -> list[ExternalBackupRun]:
    return list(
        db.scalars(select(ExternalBackupRun).order_by(ExternalBackupRun.started_at.desc()))
    )


def get_external_backup_run(db: Session, run_id: int) -> ExternalBackupRun:
    run = db.get(ExternalBackupRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="External backup run not found")
    return run


def delete_external_backup_run(db: Session, run_id: int) -> None:
    run = get_external_backup_run(db, run_id)
    if run.status in {BackupRunStatus.PENDING, BackupRunStatus.RUNNING}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Pending or running external backup runs cannot be deleted.",
        )

    db.delete(run)
    db.commit()


def cleanup_external_backup_runs(db: Session, keep_last: int = 10) -> int:
    keep_last = max(0, keep_last)
    runs = list_external_backup_runs(db)
    protected_ids = {
        run.id
        for run in runs[:keep_last]
    } | {
        run.id
        for run in runs
        if run.status in {BackupRunStatus.PENDING, BackupRunStatus.RUNNING}
    }
    deletable = [
        run
        for run in runs
        if run.status == BackupRunStatus.FAILED and run.id not in protected_ids
    ]

    for run in deletable:
        db.delete(run)
    db.commit()
    return len(deletable)


def cleanup_legacy_external_export_objects() -> dict[str, object]:
    result = get_external_backup_agent_bridge().cleanup_legacy_external_export_objects()
    return {
        "ok": result.ok,
        "message": result.message,
        "return_code": result.return_code,
    }


def inspect_external_export_objects() -> dict[str, object]:
    result = get_external_backup_agent_bridge().inspect_external_export_objects()
    return {
        "ok": result.ok,
        "message": result.message,
        "return_code": result.return_code,
        "payload": result.payload or {},
    }


def cleanup_external_export_objects() -> dict[str, object]:
    result = get_external_backup_agent_bridge().cleanup_external_export_objects()
    return {
        "ok": result.ok,
        "message": result.message,
        "return_code": result.return_code,
        "payload": result.payload or {},
    }


# Serializes the "no active run" check with run creation. Without this, two
# near-simultaneous POST /external-backups/run calls can both pass
# assert_no_active_external_backup() before either commits, creating two
# concurrent runs that then drive the same USB passthrough/PBS slot at once.
# A single uvicorn worker is used (see infra/docker/api.Dockerfile), so an
# in-process lock is sufficient and stays correct against both Postgres (prod)
# and the SQLite test database.
_external_backup_run_lock = Lock()


def run_external_backup(
    db: Session,
    disk_id: int,
    confirmation: bool,
    datastore_name: str | None = None,
    auto_eject_after_success: bool = False,
    group_filter: list[str] | None = None,
) -> ExternalBackupRun:
    if not confirmation:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="External backup execution requires explicit confirmation.",
        )

    with _external_backup_run_lock:
        assert_no_active_external_backup(db)

        disk = _get_disk_or_404(db, disk_id)
        # B25: what readiness refuses (ejected, unplugged, not approved,
        # rejected, another run) creates no run: 409 with the coded reasons.
        from app.services.backup_readiness import local_disk_reasons

        reasons = local_disk_reasons(db, disk)
        if reasons:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"message": reasons[0]["message"], "code": reasons[0]["code"], "reasons": reasons},
            )

        plan = build_external_backup_plan(disk)
        settings = get_settings()
        now = datetime.utcnow()
        run = ExternalBackupRun(
            disk_id=disk.id,
            status=BackupRunStatus.PENDING,
            started_at=now,
            finished_at=None,
            target_path=plan.target_path,
            datastore_name=datastore_name or settings.pbs_datastore,
            message=None,
            stdout_log=None,
            stderr_log=None,
            command_summary=None,
            execution_cwd=None,
            return_code=None,
            current_step="starting",
            progress_message="External backup run queued.",
            last_log_at=now,
            warning_messages=[],
            failed_groups=[],
            auto_eject_after_success=auto_eject_after_success,
            group_filter=group_filter or None,
            mode=plan.mode,
            created_at=now,
        )
        db.add(run)
        db.commit()
        db.refresh(run)

    append_external_backup_run_log(
        db,
        run.id,
        step="starting",
        message="External backup run created. Background execution will start shortly.",
    )
    db.refresh(run)
    return run


def execute_external_backup_run(run_id: int) -> None:
    with SessionLocal() as db:
        run = db.get(ExternalBackupRun, run_id)
        if run is None:
            return
        if run.status in {BackupRunStatus.SUCCESS, BackupRunStatus.FAILED}:
            return
        disk = db.get(ExternalDisk, run.disk_id)
        if disk is None:
            append_external_backup_run_log(
                db,
                run_id,
                step="failure",
                message="Disk no longer exists.",
                stream="stderr",
                line="Disk no longer exists.",
            )
            _finish_run(db, run, BackupRunStatus.FAILED, "Disk no longer exists.", return_code=None)
            notify_backup_failure(f"Disk {run.disk_id}", "failure", "Disk no longer exists.")
            return

        execution_service = get_external_backup_execution_service()

        run.status = BackupRunStatus.RUNNING
        run.current_step = "starting"
        run.progress_message = "Starting external backup execution."
        db.add(run)
        db.commit()
        db.refresh(run)
        append_external_backup_run_log(db, run.id, step="starting", message="Starting external backup execution.")

        prepare_result = None
        export_result = None
        try:
            append_external_backup_run_log(
                db,
                run.id,
                step="handoff_disk",
                message="Handing off USB disk to PBS VM.",
            )
            handoff_status = handoff_disk_to_pbs(
                db,
                disk,
                confirmation=True,
                progress=lambda step, message, line=None: append_external_backup_run_log(
                    db,
                    run.id,
                    step=step,
                    message=message,
                    line=line,
                ),
            )
            append_external_backup_run_log(
                db,
                run.id,
                step="handoff_disk",
                message=handoff_status.message,
            )
            if run.group_filter and get_settings().pbs_mode != "api":
                append_external_backup_run_log(
                    db, run.id, step="run_export",
                    message="VM selection needs PBS_MODE=api: the whole datastore is copied.",
                )
            execution_result = execution_service.execute(
                **({"group_filter": run.group_filter} if run.group_filter else {}),
                disk=disk,
                datastore_name=run.datastore_name,
                mode=run.mode,
                run_id=run.id,
                progress=lambda step, message, line=None: append_external_backup_run_log(
                    db,
                    run.id,
                    step=step,
                    message=message,
                    line=line,
                ),
            )
            prepare_result = execution_result.prepare
            export_result = execution_result.export
            run.target_path = execution_result.target_path
            run.pbs_sync_job_id = getattr(execution_result, "pbs_sync_job_id", None) or run.pbs_sync_job_id
            run.pbs_remote_id = getattr(execution_result, "pbs_remote_id", None) or run.pbs_remote_id
            run.pbs_task_upid = getattr(execution_result, "pbs_task_upid", None) or run.pbs_task_upid
            if execution_result.target_datastore_name:
                disk.pbs_datastore_name = execution_result.target_datastore_name
                disk.pbs_mount_path = execution_result.target_path
                disk.pbs_filesystem_type = "ext4"
                disk.prepared_as_pbs_datastore = True
                db.add(disk)
                db.commit()
                db.refresh(disk)
                from app.services.disks import refresh_disk_filesystem_usage_from_pbs
                refresh_disk_filesystem_usage_from_pbs(db, disk)

            # B24: the run stays RUNNING until the optional eject (step 6) is
            # done, so the backup and its eject are one run and one modal.
            run.message = f"{handoff_status.message} {export_result.message}".strip()
            run.stdout_log = _merge_logs(
                run.stdout_log,
                handoff_status.message,
                prepare_result.stdout_log,
                export_result.stdout_log,
            )
            run.stderr_log = _merge_logs(run.stderr_log, prepare_result.stderr_log, export_result.stderr_log)
            run.command_summary = _merge_logs(prepare_result.command_summary, export_result.command_summary)
            run.execution_cwd = _merge_logs(prepare_result.execution_cwd, export_result.execution_cwd)
            run.return_code = export_result.return_code
            _apply_progress_text(run, export_result.stdout_log)
            run.current_step = "success"
            run.progress_message = run.message
            run.last_log_at = datetime.utcnow()
            backup_message = run.message
            append_external_backup_run_log(db, run.id, step="success", message=run.message)

            if run.auto_eject_after_success:
                from app.services import disk_eject

                if disk_eject.is_disk_auto_eject_eligible(disk):
                    disk_eject.eject_disk_within_run(db, disk, run)
                    db.refresh(run)
                    db.refresh(disk)
                else:
                    append_external_backup_run_log(
                        db,
                        run.id,
                        step="success",
                        message=(
                            "Automatic eject was requested, but this disk mode does not support it yet "
                            "(only dedicated PBS datastore disks can be auto-ejected today)."
                        ),
                    )
            run.status = BackupRunStatus.SUCCESS
            run.finished_at = datetime.utcnow()
            run.message = backup_message
            run.progress_message = backup_message
        except AgentCommandError as exc:
            run.status = BackupRunStatus.FAILED
            run.finished_at = datetime.utcnow()
            run.message = str(exc)
            run.stdout_log = _merge_logs(
                run.stdout_log,
                prepare_result.stdout_log if prepare_result else None,
                exc.stdout_log,
            )
            run.stderr_log = _merge_logs(
                run.stderr_log,
                prepare_result.stderr_log if prepare_result else None,
                exc.stderr_log,
                str(exc),
            )
            run.command_summary = _merge_logs(
                prepare_result.command_summary if prepare_result else None,
                exc.command_summary,
            )
            run.execution_cwd = _merge_logs(
                prepare_result.execution_cwd if prepare_result else None,
                exc.execution_cwd,
            )
            run.return_code = exc.return_code
            _apply_progress_text(run, exc.stdout_log)
            _apply_progress_text(run, exc.stderr_log)
            summary = summarize_pbs_failure(str(exc), run.failed_groups)
            if summary:
                run.message = summary
                run.progress_message = summary
            run.current_step = "failure"
            run.progress_message = run.progress_message or str(exc)
            run.last_log_at = datetime.utcnow()
            append_external_backup_run_log(db, run.id, step="failure", message=str(exc), stream="stderr", line=str(exc))
        except HTTPException as exc:
            # A coded failure (e.g. usb_device_not_found) keeps its code as the
            # step, translated by the UI; the technical detail goes to the log.
            code = getattr(exc, "code", None)
            technical = getattr(exc, "technical", None)
            run.status = BackupRunStatus.FAILED
            run.finished_at = datetime.utcnow()
            run.message = str(exc.detail)
            run.return_code = None
            run.current_step = code or "failure"
            run.progress_message = str(exc.detail)
            run.last_log_at = datetime.utcnow()
            # One ERR line, coded (translated by the UI). A coded handoff error
            # already reported its useful technical detail as an info line.
            append_external_backup_run_log(
                db,
                run.id,
                step=code or "failure",
                message=str(exc.detail),
                stream="stderr",
                line=None if code else technical,
            )
        except RuntimeError as exc:
            run.status = BackupRunStatus.FAILED
            run.finished_at = datetime.utcnow()
            run.message = str(exc)
            run.stdout_log = _merge_logs(
                run.stdout_log,
                prepare_result.stdout_log if prepare_result else None,
                export_result.stdout_log if export_result else None,
            )
            run.stderr_log = _merge_logs(
                run.stderr_log,
                prepare_result.stderr_log if prepare_result else None,
                export_result.stderr_log if export_result else None,
                str(exc),
            )
            run.command_summary = _merge_logs(
                prepare_result.command_summary if prepare_result else None,
                export_result.command_summary if export_result else None,
            )
            run.execution_cwd = _merge_logs(
                prepare_result.execution_cwd if prepare_result else None,
                export_result.execution_cwd if export_result else None,
            )
            run.return_code = export_result.return_code if export_result else prepare_result.return_code if prepare_result else None
            _apply_progress_text(run, export_result.stdout_log if export_result else None)
            _apply_progress_text(run, export_result.stderr_log if export_result else None)
            summary = summarize_pbs_failure(str(exc), run.failed_groups)
            if summary:
                run.message = summary
                run.progress_message = summary
            run.current_step = "failure"
            run.progress_message = run.progress_message or str(exc)
            run.last_log_at = datetime.utcnow()
            append_external_backup_run_log(db, run.id, step="failure", message=str(exc), stream="stderr", line=str(exc))
        except Exception as exc:
            run.status = BackupRunStatus.FAILED
            run.finished_at = datetime.utcnow()
            run.message = str(exc)
            run.stderr_log = _merge_logs(run.stderr_log, str(exc))
            run.return_code = None
            run.current_step = "failure"
            run.progress_message = str(exc)
            run.last_log_at = datetime.utcnow()
            append_external_backup_run_log(db, run.id, step="failure", message=str(exc), stream="stderr", line=str(exc))

        db.add(run)
        db.commit()
        disk_label = _format_disk_notification_label(disk, run.datastore_name)
        if run.status == BackupRunStatus.SUCCESS:
            notify_backup_success(disk_label)
        elif run.status == BackupRunStatus.FAILED:
            notify_backup_failure(disk_label, run.current_step, run.message)


def append_external_backup_run_log(
    db: Session,
    run_id: int,
    *,
    step: str | None,
    message: str | None,
    stream: Literal["stdout", "stderr"] = "stdout",
    line: str | None = None,
    command: str | None = None,
) -> ExternalBackupRun:
    run = get_external_backup_run(db, run_id)
    now = datetime.utcnow()
    clean_step = (step or run.current_step or "progress").strip()[:128]
    clean_message = (message or line or command or "Progress update.").strip()
    entry_parts = [f"[{now.isoformat(timespec='seconds')}Z]", clean_step]
    if command:
        entry_parts.append(f"command={command}")
    if clean_message:
        entry_parts.append(clean_message)
    if line and line != clean_message:
        entry_parts.append(line.rstrip())
    entry = " ".join(entry_parts)
    progress_line = line or clean_message

    if is_progress_line(progress_line):
        # B22: "percentage done" / "Chunkstore create: n%" feed the progress bar,
        # they are not journal lines.
        run.current_step = clean_step
        run.last_log_at = now
        _apply_progress_line(run, progress_line, now)
        db.add(run)
        db.commit()
        db.refresh(run)
        return run
    previous = (run.stderr_log if stream == "stderr" else run.stdout_log) or ""
    if _entry_body(previous.rsplit("\n", 1)[-1]) == _entry_body(entry):
        # B22: identical consecutive line (TASK OK, Wiping…): kept once.
        run.last_log_at = now
        _apply_progress_line(run, progress_line, now)
        db.add(run)
        db.commit()
        db.refresh(run)
        return run

    if stream == "stderr":
        run.stderr_log = _append_log(run.stderr_log, entry)
    else:
        run.stdout_log = _append_log(run.stdout_log, entry)
    run.current_step = clean_step
    run.progress_message = clean_message
    run.last_log_at = now
    _apply_progress_line(run, line or clean_message, now)
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def assert_no_active_external_backup(db: Session) -> None:
    active_run = db.scalar(
        select(ExternalBackupRun)
        .where(ExternalBackupRun.status.in_([BackupRunStatus.PENDING, BackupRunStatus.RUNNING]))
        .order_by(ExternalBackupRun.started_at.desc())
        .limit(1)
    )
    if active_run is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "Un backup externe est deja en cours",
                "active_run_id": active_run.id,
            },
        )

    bridge = get_external_backup_agent_bridge()
    if hasattr(bridge, "assert_external_backup_capabilities"):
        try:
            bridge.assert_external_backup_capabilities()
        except AgentCompatibilityError as exc:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    try:
        result = bridge.inspect_external_export_objects()
    except AgentCommandError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    payload = result.payload or {}
    active = bool(payload.get("active"))
    active_run_id = payload.get("active_run_id")
    if active:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "Un backup externe est deja en cours",
                "active_run_id": active_run_id if isinstance(active_run_id, int) else None,
            },
        )


def _finish_run(
    db: Session,
    run: ExternalBackupRun,
    status_value: BackupRunStatus,
    message: str,
    *,
    return_code: int | None,
) -> None:
    run.status = status_value
    run.finished_at = datetime.utcnow()
    run.message = message
    run.progress_message = message
    run.return_code = return_code
    run.last_log_at = datetime.utcnow()
    db.add(run)
    db.commit()


def _format_disk_notification_label(disk: ExternalDisk, datastore_name: str | None = None) -> str:
    label = disk.display_name or disk.serial_number
    details = [f"serial {disk.serial_number}"]
    if disk.model_name:
        details.append(f"model {disk.model_name}")
    if datastore_name:
        details.append(f"datastore {datastore_name}")
    if disk.source:
        details.append(f"source {disk.source}")
    return f"{label} ({', '.join(details)})"


def _merge_logs(*values: str | None) -> str | None:
    merged = "\n\n".join(value for value in values if value)
    return merged or None


def _entry_body(entry: str) -> str:
    """Log line without its leading `[timestamp]`."""
    text = entry.strip()
    if text.startswith("[") and "] " in text:
        return text.split("] ", 1)[1]
    return text


def _append_log(existing: str | None, entry: str) -> str:
    if not existing:
        return entry
    return f"{existing}\n{entry}"


def _apply_progress_text(run: ExternalBackupRun, text: str | None) -> None:
    if not text:
        return
    now = datetime.utcnow()
    for line in text.splitlines():
        _apply_progress_line(run, line, now)


def _apply_progress_line(run: ExternalBackupRun, line: str | None, now: datetime) -> None:
    update = parse_pbs_progress_line(line)
    if update.sync_started:
        # A new phase starts: the datastore creation percentage is not the sync's.
        run.progress_percent = 0.0
    if update.chunkstore_percent is not None:
        run.progress_percent = update.chunkstore_percent
    if update.progress_percent is not None:
        run.progress_percent = update.progress_percent
    if update.total_groups is not None:
        run.total_groups = update.total_groups
    if update.completed_groups is not None:
        run.completed_groups = update.completed_groups
    if update.current_group is not None:
        run.current_group = update.current_group
    if update.current_snapshot is not None:
        run.current_snapshot = update.current_snapshot
    if update.current_archive is not None:
        run.current_archive = update.current_archive
    if update.summary:
        # B23: the totals of the sync come from PBS's "Summary:" line.
        if update.downloaded_bytes is not None:
            run.downloaded_bytes = update.downloaded_bytes
        if update.current_speed is not None:
            run.current_speed = update.current_speed
    elif update.downloaded_bytes is not None and "Summary:" not in (run.stdout_log or ""):
        # Live: each "downloaded" line is one archive → cumulative transferred bytes.
        run.downloaded_bytes = (run.downloaded_bytes or 0) + update.downloaded_bytes
        if update.current_speed is not None:
            run.current_speed = update.current_speed
    if update.pbs_sync_job_id is not None:
        run.pbs_sync_job_id = update.pbs_sync_job_id
    if update.pbs_remote_id is not None:
        run.pbs_remote_id = update.pbs_remote_id
    if update.pbs_task_upid is not None:
        run.pbs_task_upid = update.pbs_task_upid
    if update.has_progress or update.task_ok or update.task_error or update.partial_failure:
        run.last_progress_at = now
        run.elapsed_seconds = max(0, int((now - run.started_at).total_seconds()))
    if update.warnings:
        existing = list(run.warning_messages or [])
        for warning in update.warnings:
            if warning not in existing:
                existing.append(warning)
        run.warning_messages = existing
    if update.failed_groups:
        existing_failed = list(run.failed_groups or [])
        seen = {(item.get("group"), item.get("reason")) for item in existing_failed}
        for failed in update.failed_groups:
            key = (failed.get("group"), failed.get("reason"))
            if key not in seen:
                existing_failed.append(failed)
                seen.add(key)
        run.failed_groups = existing_failed
    if update.partial_failure:
        summary = summarize_pbs_failure("sync failed with some errors", run.failed_groups)
        run.progress_message = summary or "Synchronisation terminee avec erreurs"
