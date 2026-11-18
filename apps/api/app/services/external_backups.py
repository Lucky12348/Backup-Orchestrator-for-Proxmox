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
from app.services import post_sync
from app.services import notifications, run_control
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
    target_path, target_datastore = plan.target_path, None
    if settings.pbs_mode == "api":
        # B48: the disk's own removable datastore, mounted by PBS on
        # /mnt/datastore/<name> (not the agent-mode "…/pbs-datastore").
        from app.services.external_backup_execution import build_dedicated_datastore_name
        from app.services.pbs_guards import fit_datastore_name

        target_datastore = fit_datastore_name(disk.pbs_datastore_name or build_dedicated_datastore_name(disk.serial_number))
        target_path = f"/mnt/datastore/{target_datastore}"
    return {
        "target_path": target_path,
        "target_datastore": target_datastore,
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

    _detach_planned_runs(db, [run.id])
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

    _detach_planned_runs(db, [run.id for run in deletable])
    for run in deletable:
        db.delete(run)
    db.commit()
    return len(deletable)


def _detach_planned_runs(db: Session, run_ids: list[int]) -> None:
    """B29: `scheduled_backup_runs.backup_run_id` references the run. Keep the
    planning history, drop the link (PostgreSQL refused the DELETE: 500)."""
    if not run_ids:
        return
    from sqlalchemy import update

    from app.models import ScheduledBackupRun

    db.execute(update(ScheduledBackupRun).where(ScheduledBackupRun.backup_run_id.in_(run_ids)).values(backup_run_id=None))
    db.flush()


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

# B44: the only journal steps accepted once a run is interrupted.
INTERRUPTION_STEPS = frozenset({"disk_pulled"})


class RunInterrupted(RuntimeError):
    """The disk was pulled out during the run (`run.interrupted` is set)."""


def check_interrupted(db: Session, run: ExternalBackupRun) -> None:
    """Checkpoint between two steps: disk pulled out (B44) or run cancelled (B56)."""
    db.refresh(run, attribute_names=["interrupted", "status"])
    if run.interrupted:
        raise RunInterrupted(run.interrupted)
    if run_control.is_requested(run.id):
        raise run_control.RunCancelled()


def run_external_backup(
    db: Session,
    disk_id: int,
    confirmation: bool,
    datastore_name: str | None = None,
    auto_eject_after_success: bool = False,
    group_filter: list[str] | None = None,
    verify_after_backup: bool = False,
    smart_override: bool = False,
    verify_only: bool = False,
    repair: bool = False,
    confirm_lost: bool = False,
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

        reasons = local_disk_reasons(db, disk, smart_override=smart_override)
        estimate = None
        if verify_only or repair:
            reasons.extend(_verify_only_reasons(disk))
        if repair:
            reasons.extend(_repair_reasons(disk, confirm_lost))
        else:
            # Phase 5f: estimated copy vs free space (PBS API; unknown = no refusal).
            from app.services.capacity import check_space, space_reason

            estimate = check_space(db, disk, group_filter=group_filter, datastore=datastore_name)
            if reason := space_reason(estimate):
                reasons.append(reason)
        reasons.extend(_token_privilege_reasons(db))
        if reasons:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"message": reasons[0]["message"], "code": reasons[0]["code"], "reasons": reasons},
            )
        # B49: the disk must be there NOW (asked to the host agent), not only
        # in the last report: absent = no run (button and planning alike).
        from app.services.disk_presence import require_present

        require_present(db, disk)

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
            verify_after_backup=verify_after_backup or verify_only,
            verify_only=verify_only or None,
            repair=repair or None,
            mode=plan.mode,
            created_at=now,
        )
        db.add(run)
        db.commit()
        db.refresh(run)

    # Each run type has its own first line (B: not "external backup" for a verification).
    created = {
        "repair": "Repair run created, starting shortly.",
        "verify": "Verification run created, starting shortly.",
    }.get("repair" if repair else "verify" if verify_only else "", "External backup run created. Background execution will start shortly.")
    append_external_backup_run_log(db, run.id, step="starting", message=created)
    from app.services import disk_profile

    if smart_override and disk.smart_status == "danger":
        append_external_backup_run_log(
            db, run.id, step="starting",
            message="SMART reports this disk as failing: backup started anyway on explicit confirmation.",
            stream="stderr",
        )
    if verify_after_backup and not verify_only:
        append_external_backup_run_log(
            db, run.id, step="starting", message="Verification of the copy requested: it runs after the sync."
        )
    if disk_profile.is_usb2(disk):
        append_external_backup_run_log(
            db, run.id, step="starting",
            message=f"USB 2.0 link ({disk.usb_speed_mbps} Mbit/s): the copy will be about 10× slower.",
        )
    if estimate and estimate.get("known"):
        append_external_backup_run_log(
            db, run.id, step="starting",
            message=(
                f"Space check: about {estimate['needed_bytes'] / 1024**3:.1f} GiB to copy, "
                f"{estimate['free_bytes'] / 1024**3:.1f} GiB free."
            ),
        )
    db.refresh(run)
    return run


def _token_privilege_reasons(db: Session) -> list[dict[str, object]]:
    """PBS_MODE=api: the PBO token must hold every privilege of the disk path
    (services/pbs_permissions.py); unknown = no refusal."""
    from app.services.settings_service import effective_settings

    settings = effective_settings(db)
    if settings.pbs_mode != "api" or not settings.pbs_api_url or not settings.pbs_token_id:
        return []
    from app.services import pbs_permissions
    from app.services.pbs_client import PBSClient

    found = pbs_permissions.check(settings, PBSClient(settings))
    return [found] if found else []


def _repair_reasons(disk: ExternalDisk, confirm_lost: bool) -> list[dict[str, object]]:
    damaged = disk.damaged_snapshots or []
    if not damaged:
        return [{"code": "repair_nothing", "message": "No damaged version is recorded on this disk."}]
    lost = [item for item in damaged if item.get("in_source") is False]
    if lost and not confirm_lost:
        return [
            {
                "code": "repair_confirm_lost",
                "message": "Some damaged versions no longer exist in the source: they only exist, damaged, on this disk. Confirm their deletion.",
                "lost": [f"{item['group']} {item['time']}" for item in lost],
            }
        ]
    return []


def _verify_only_reasons(disk: ExternalDisk) -> list[dict[str, str]]:
    if get_settings().pbs_mode != "api":
        return [{"code": "verify_api_mode_only", "message": "A verify-only run needs PBS_MODE=api."}]
    if not (disk.prepared_as_pbs_datastore and disk.pbs_datastore_name):
        return [{"code": "verify_needs_datastore", "message": "This disk holds no PBO datastore to verify."}]
    return []


def execute_external_backup_run(run_id: int) -> None:
    with run_control.bound(run_id):
        try:
            _execute_external_backup_run(run_id)
        finally:
            run_control.clear(run_id)


def _execute_external_backup_run(run_id: int) -> None:
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
            notifications.run_finished(run, None)
            return

        execution_service = get_external_backup_execution_service()

        run.status = BackupRunStatus.RUNNING
        run.current_step = "starting"
        run.progress_message = "Starting external backup execution."
        db.add(run)
        db.commit()
        db.refresh(run)
        starting = "Starting the repair." if run.repair else "Starting the verification." if run.verify_only else "Starting external backup execution."
        append_external_backup_run_log(db, run.id, step="starting", message=starting)

        prepare_result = None
        export_result = None
        try:
            check_interrupted(db, run)
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

            def log(**kwargs) -> None:
                append_external_backup_run_log(db, run.id, **kwargs)

            check_interrupted(db, run)

            if run.repair:
                from app.services.repair_run import execute_repair

                run.message = execute_repair(db, run, disk, handoff_status.message, log)
                run.current_step = "success"
                run.progress_message = run.message
                run.last_log_at = datetime.utcnow()
                backup_message = run.message
            elif run.verify_only:
                run.message = _execute_verify_only(db, run, disk, handoff_status.message, log)
                run.current_step = "success"
                run.progress_message = run.message
                run.last_log_at = datetime.utcnow()
                backup_message = run.message
            else:
                if run.group_filter and get_settings().pbs_mode != "api":
                    append_external_backup_run_log(
                        db, run.id, step="run_export",
                        message="VM selection needs PBS_MODE=api: the whole datastore is copied.",
                    )
                def verify_first(store: str) -> None:
                    # B46: the disk was pulled out before its datastore was
                    # unmounted: check everything before copying onto it.
                    db.refresh(disk)
                    if not disk.verify_required:
                        return
                    log(step="verify", message="Verification required first: the disk was pulled out before its datastore was unmounted.")
                    bridge = _post_sync_bridge(execution_service)
                    post_sync.verify(bridge, run, store, log, copied=None, verify_all=True, db=db, disk=disk)
                    _verification_passed(db, disk)

                execution_result = execution_service.execute(
                    before_export=verify_first,
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
                # The handoff message is already in the journal; the preparation
                # details (target paths...) become timestamped lines, the PBS task
                # lines were streamed live (B22: no raw block merged at the end).
                _journal_block(db, run, "prepare_datastore", prepare_result.stdout_log)
                _journal_block(db, run, "sync_job", export_result.stdout_log)
                run.stderr_log = _merge_logs(run.stderr_log, prepare_result.stderr_log, export_result.stderr_log)
                run.command_summary = _merge_logs(prepare_result.command_summary, export_result.command_summary)
                run.execution_cwd = _merge_logs(prepare_result.execution_cwd, export_result.execution_cwd)
                run.return_code = export_result.return_code
                _apply_progress_text(run, export_result.stdout_log)
                # Phase 5f: Cleanup (retention + GC), contents, Verification.
                store = execution_result.target_datastore_name
                check_interrupted(db, run)
                if store:
                    bridge = _post_sync_bridge(execution_service)
                    post_sync.cleanup(bridge, run, disk, store, log)
                    check_interrupted(db, run)
                    after = post_sync.refresh_contents(db, bridge, run, disk, store, log)
                    check_interrupted(db, run)
                    if run.verify_after_backup:
                        copied, verify_all = post_sync.copied_keys(execution_result.pre_sync_snapshots, after)
                        post_sync.verify(bridge, run, store, log, copied=copied, verify_all=verify_all, db=db, disk=disk)
                elif run.verify_after_backup:
                    log(step="verify", message="Verification skipped: no new version was copied.")
                run.current_step = "success"
                run.progress_message = run.message
                run.last_log_at = datetime.utcnow()
                backup_message = run.message
            check_interrupted(db, run)
            append_external_backup_run_log(db, run.id, step="success", message=run.message)

            if (run.verify_only or run.repair) and not run.auto_eject_after_success:
                # B45: a verification ends by releasing the disk (unmount +
                # detach, no spin-down): it is connected again, ready to use.
                from app.services import disk_eject

                disk_eject.release_disk_within_run(db, disk, run)
                db.refresh(run)
                db.refresh(disk)
            elif run.auto_eject_after_success:
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
        except RunInterrupted:
            pass  # status, step and message were set when the removal was detected
        except run_control.RunCancelled:
            pass  # finished below (_finish_cancelled), once no PBS task runs any more
        except post_sync.VerifyFailed as exc:
            run.status = BackupRunStatus.FAILED
            run.finished_at = datetime.utcnow()
            run.message = str(exc)
            run.return_code = None
            run.current_step = "verify_failed"
            run.progress_message = str(exc)
            run.last_log_at = datetime.utcnow()
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

        db.refresh(run, attribute_names=["interrupted"])
        if run.interrupted:
            db.refresh(run, attribute_names=["stdout_log", "stderr_log"])  # the journal as written, not a stale merge
            _apply_interruption(run)
        elif run_control.is_requested(run.id) and run.status != BackupRunStatus.SUCCESS:
            _finish_cancelled(db, run, disk)
        _close_released_mappings(db, disk)
        db.add(run)
        db.commit()
        notifications.run_finished(run, disk)


INTERRUPTION_MESSAGES = {
    "during_sync": "Interrupted: the disk was pulled out during the copy. The copy is incomplete and not guaranteed: the next backup starts with a verification.",
    # B46: a copy is finished only once its datastore is unmounted.
    "after_sync": "Interrupted: the disk was pulled out before its datastore was unmounted. The copy is not guaranteed: a verification is required.",
}


CANCELLED_MESSAGE = "Cancelled by the operator."
# Steps from which the run may have written versions on the disk.
_WRITING_STEPS = frozenset({"sync_job", "run_export", "cleanup", "prune", "gc", "contents", "verify", "repair"})


def _finish_cancelled(db: Session, run: ExternalBackupRun, disk: ExternalDisk) -> None:
    """B56: the operator cancelled the run. Its PBS task is already stopped
    (wait_for_task); release the disk (unmount on PBS, USB removed from the
    VM) so it is back on the host, then end the run as cancelled. A clean
    unmount leaves a consistent datastore (sync-level=file; finished versions
    are kept, PBS drops the one it was copying): no verification is required.
    Only a failed release after something was written recommends one."""
    from app.services import disk_eject

    db.refresh(run)
    db.refresh(disk)
    wrote = bool(run.pbs_task_upid) or (run.current_step or "") in _WRITING_STEPS
    released = True
    if disk.pbs_handoff_slot:
        released = disk_eject.release_disk_within_run(db, disk, run, datastore_may_be_missing=True)
        db.refresh(run)
        db.refresh(disk)
    if not released and wrote:
        disk.verify_recommended = True
        db.add(disk)
    run.status = BackupRunStatus.FAILED
    run.current_step = run_control.CANCELLED_STEP
    run.message = CANCELLED_MESSAGE
    run.progress_message = CANCELLED_MESSAGE
    run.finished_at = datetime.utcnow()
    run.last_log_at = datetime.utcnow()
    db.add(run)
    db.commit()
    append_external_backup_run_log(db, run.id, step=run_control.CANCELLED_STEP, message=CANCELLED_MESSAGE)
    db.refresh(run)


def _apply_interruption(run: ExternalBackupRun) -> None:
    message = INTERRUPTION_MESSAGES.get(run.interrupted or "", INTERRUPTION_MESSAGES["during_sync"])
    run.status = BackupRunStatus.FAILED
    run.current_step = "disk_pulled"
    run.message = message
    run.progress_message = message
    run.finished_at = run.finished_at or datetime.utcnow()


def _close_released_mappings(db: Session, disk: ExternalDisk) -> None:
    """End of every run (B43): a disk no longer handed to PBS has no open USB mapping."""
    if disk.pbs_handoff_slot:
        return
    from app.models import PbsUsbMapping

    for row in db.scalars(select(PbsUsbMapping).where(PbsUsbMapping.disk_id == disk.id, PbsUsbMapping.released_at.is_(None))):
        row.released_at = datetime.utcnow()
        row.release_reason = "run_end"


def _post_sync_bridge(execution_service):
    bridge = getattr(execution_service, "_bridge", None)
    return bridge if hasattr(bridge, "pbs_command") else get_external_backup_agent_bridge()


def _execute_verify_only(db: Session, run: ExternalBackupRun, disk: ExternalDisk, handoff_message: str, log) -> str:
    """Hand-off done: reuse the disk's datastore (never format), verify every
    snapshot, refresh the contents, clear "verification recommended"."""
    from app.services.external_backup_execution import build_dedicated_datastore_name

    log(step="starting", message="Verify-only run: the disk's datastore is checked, nothing is copied.")
    bridge = get_external_backup_agent_bridge()
    store = disk.pbs_datastore_name or build_dedicated_datastore_name(disk.serial_number)
    inspect = bridge.inspect_disk_on_pbs(disk)
    if not inspect.ok:
        raise RuntimeError(inspect.message)
    log(step="prepare_dedicated_datastore", message="Preparing or reusing dedicated PBS datastore.")
    prepared = bridge.prepare_dedicated_pbs_datastore(disk, store, run_id=run.id, reuse_only=True)
    if not prepared.ok:
        raise RuntimeError(prepared.message)
    store = str((prepared.payload or {}).get("datastore_name") or store)
    try:
        post_sync.verify(bridge, run, store, log, copied=None, verify_all=True, db=db, disk=disk)
    except post_sync.VerifyFailed:
        # B47: the Release step runs even after a failed verification.
        if not run.auto_eject_after_success:
            from app.services import disk_eject

            disk_eject.release_disk_within_run(db, disk, run)
        raise
    post_sync.refresh_contents(db, bridge, run, disk, store, log)
    _verification_passed(db, disk)
    return f"Verification completed on `{store}`."


def _verification_passed(db: Session, disk: ExternalDisk) -> None:
    """A full verification succeeded: nothing required, nothing damaged."""
    from app.services import disk_guards

    disk_guards.clear_verify_recommended(disk)
    disk.verify_required = None
    disk.damaged_snapshots = None
    db.add(disk)
    db.commit()


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
    # Other sessions append to the same run (PBS API backend progress, agent
    # callbacks): reload the logs before appending, or this session's copy
    # (expire_on_commit=False) would overwrite their lines. Pending changes
    # of this session are flushed first so the refresh cannot drop them.
    db.refresh(run, attribute_names=["interrupted"])
    if run.interrupted and step not in INTERRUPTION_STEPS:
        # B44: the run stopped when the disk was pulled out: no normal step
        # after, and an error handler's stale copy of the logs is dropped.
        db.refresh(run, attribute_names=["stdout_log", "stderr_log"])
        return run
    db.flush()
    db.refresh(run, attribute_names=["stdout_log", "stderr_log"])
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


def _journal_block(db: Session, run: ExternalBackupRun, step: str, text: str | None) -> None:
    """Appends the lines of a result block that are not in the journal yet,
    each one timestamped (instead of a raw block without timestamps)."""
    from app.services.run_log import _PBS_LINE_RE

    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line in (run.stdout_log or ""):
            continue
        if _PBS_LINE_RE.match(line) or is_progress_line(line):
            continue  # PBS task lines were streamed live; progress is not journal
        append_external_backup_run_log(db, run.id, step=step, message=line)


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
