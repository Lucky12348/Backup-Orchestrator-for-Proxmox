import secrets

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, status
from sqlalchemy import select

from app.api.dependencies import DbSession
from app.auth import CurrentUser
from app.core.config import get_settings
from app.models import BackupRunStatus, ExternalDisk
from app.schemas import (
    ExternalBackupRunLogRequest,
    ExternalBackupRunRead,
    ExternalBackupRunRequest,
    ExternalBackupRunSummaryRead,
)
from app.services.agent_registry import authenticate_callback, has_active_agent
from app.services.external_backups import (
    append_external_backup_run_log,
    cleanup_external_backup_runs,
    cleanup_external_export_objects,
    cleanup_legacy_external_export_objects,
    delete_external_backup_run,
    execute_external_backup_run,
    get_external_backup_preview,
    get_external_backup_run,
    inspect_external_export_objects,
    list_external_backup_runs,
    run_external_backup,
)

router = APIRouter(prefix="/external-backups", tags=["external-backups"])
public_callback_router = APIRouter(prefix="/external-backups", tags=["external-backups"])


@router.get("/preview/{disk_id}")
def get_preview(disk_id: int, db: DbSession) -> dict[str, str | bool | int | None]:
    return get_external_backup_preview(db, disk_id)


@router.post("/run", response_model=ExternalBackupRunSummaryRead)
def start_run(
    payload: ExternalBackupRunRequest,
    background_tasks: BackgroundTasks,
    db: DbSession,
) -> ExternalBackupRunSummaryRead:
    run = run_external_backup(
        db,
        payload.disk_id,
        payload.confirmation,
        auto_eject_after_success=payload.auto_eject_after_success,
        verify_after_backup=payload.verify_after_backup,
        smart_override=payload.smart_override,
    )
    background_tasks.add_task(execute_external_backup_run, run.id)
    disk = db.get(ExternalDisk, run.disk_id)
    return _build_summary(run, disk.display_name if disk is not None else f"Disk {run.disk_id}", disk)


@router.get("/readiness/{disk_id}")
def get_readiness(disk_id: int, db: DbSession) -> dict[str, object]:
    """Ready to start an external backup on this disk? Reasons are stable codes
    (translated by the UI) with an English message."""
    from app.services.backup_readiness import external_backup_readiness

    disk = db.get(ExternalDisk, disk_id)
    if disk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Disk not found")
    return external_backup_readiness(db, disk)


@router.get("/runs", response_model=list[ExternalBackupRunSummaryRead])
def get_runs(db: DbSession) -> list[ExternalBackupRunSummaryRead]:
    runs = list_external_backup_runs(db)
    disks = {
        disk.id: disk
        for disk in db.scalars(select(ExternalDisk).where(ExternalDisk.id.in_([run.disk_id for run in runs])))
    }
    return [
        _build_summary(
            run,
            disks[run.disk_id].display_name if run.disk_id in disks else f"Disk {run.disk_id}",
            disks.get(run.disk_id),
        )
        for run in runs
    ]


@router.delete("/runs/cleanup")
def cleanup_runs(db: DbSession, keep_last: int = 10) -> dict[str, int]:
    deleted = cleanup_external_backup_runs(db, keep_last=keep_last)
    return {"deleted": deleted, "keep_last": keep_last}


@router.post("/legacy-objects/cleanup")
def cleanup_legacy_objects() -> dict[str, object]:
    return cleanup_legacy_external_export_objects()


@router.get("/temporary-objects/status")
def get_temporary_objects_status() -> dict[str, object]:
    return inspect_external_export_objects()


@router.post("/temporary-objects/cleanup")
def cleanup_temporary_objects() -> dict[str, object]:
    return cleanup_external_export_objects()


@router.get("/runs/{run_id}", response_model=ExternalBackupRunRead)
def get_run(run_id: int, db: DbSession) -> ExternalBackupRunRead:
    run = get_external_backup_run(db, run_id)
    read = ExternalBackupRunRead.model_validate(run)
    return read.model_copy(update=_run_extras(run, db.get(ExternalDisk, run.disk_id)))


@router.get("/runs/{run_id}/log")
def get_run_log(run_id: int, db: DbSession) -> dict[str, object]:
    """Readable journal (progress lines removed, duplicates collapsed, PBO
    messages coded for translation, PBS lines marked) + Summary statistics."""
    from app.services.run_log import run_journal

    run = get_external_backup_run(db, run_id)
    return run_journal(run, db.get(ExternalDisk, run.disk_id))


@router.post("/runs/{run_id}/cancel", status_code=status.HTTP_202_ACCEPTED)
def cancel_run(run_id: int, db: DbSession, username: CurrentUser) -> dict[str, object]:
    """Stop the running PBS sync task (API mode). Snapshots already copied stay
    valid; the run then fails and the disk is left attached (eject it after)."""
    from app.services.pbs_client import PBSClient

    run = get_external_backup_run(db, run_id)
    if run.status not in (BackupRunStatus.PENDING, BackupRunStatus.RUNNING):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"code": "run_not_running", "message": "This run is not running."})
    settings = get_settings()
    if settings.pbs_mode != "api" or not run.pbs_task_upid:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "cancel_not_supported", "message": "This run cannot be cancelled now (no PBS task to stop yet, or PBS_MODE=agent)."},
        )
    append_external_backup_run_log(db, run.id, step=run.current_step, message=f"Cancellation requested by {username}.")
    try:
        PBSClient(settings).stop_task(run.pbs_task_upid)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail={"code": "cancel_failed", "message": f"PBS refused to stop the task: {exc}"}
        ) from exc
    return {"run_id": run.id, "cancel_requested": True}


@router.delete("/runs/{run_id}", status_code=204)
def delete_run(run_id: int, db: DbSession) -> None:
    delete_external_backup_run(db, run_id)


def _require_run_log_token(db: DbSession, x_agent_token: str | None) -> None:
    """Registered PBS agent: its own callback token. Legacy: PBS_AGENT_TOKEN
    (only while no PBS agent is registered, since a registered agent's
    PBS_AGENT_TOKEN is the app -> agent direction)."""
    if authenticate_callback(db, x_agent_token, roles=("pbs",)) is not None:
        return
    settings = get_settings()
    legacy_token = "" if settings.pbs_agent_fingerprint else settings.pbs_agent_token
    if not legacy_token and not has_active_agent(db, "pbs"):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="PBS_AGENT_TOKEN is not configured.",
        )
    if x_agent_token is None or not legacy_token or not secrets.compare_digest(x_agent_token, legacy_token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid agent token.")


@public_callback_router.post("/runs/{run_id}/log", response_model=ExternalBackupRunRead)
def append_run_log(
    run_id: int,
    payload: ExternalBackupRunLogRequest,
    db: DbSession,
    x_agent_token: str | None = Header(default=None),
) -> ExternalBackupRunRead:
    _require_run_log_token(db, x_agent_token)

    stream = "stderr" if payload.stderr_line else "stdout"
    run = append_external_backup_run_log(
        db,
        run_id,
        step=payload.step,
        message=payload.message,
        stream=stream,
        line=payload.stderr_line or payload.stdout_line,
        command=payload.command,
    )
    return ExternalBackupRunRead.model_validate(run)


def _build_summary(run, disk_name: str, disk=None) -> ExternalBackupRunSummaryRead:  # noqa: ANN001
    return ExternalBackupRunSummaryRead(
        id=run.id,
        disk_id=run.disk_id,
        disk_name=disk_name,
        status=run.status,
        started_at=run.started_at,
        finished_at=run.finished_at,
        target_path=run.target_path,
        datastore_name=run.datastore_name,
        message=run.message,
        stdout_log=run.stdout_log,
        stderr_log=run.stderr_log,
        command_summary=run.command_summary,
        execution_cwd=run.execution_cwd,
        return_code=run.return_code,
        current_step=run.current_step,
        verify_after_backup=run.verify_after_backup,
        verify_only=run.verify_only,
        progress_message=run.progress_message,
        last_log_at=run.last_log_at,
        progress_percent=run.progress_percent,
        total_groups=run.total_groups,
        completed_groups=run.completed_groups,
        current_group=run.current_group,
        current_snapshot=run.current_snapshot,
        current_archive=run.current_archive,
        downloaded_bytes=run.downloaded_bytes,
        current_speed=run.current_speed,
        last_progress_at=run.last_progress_at,
        warning_messages=run.warning_messages,
        failed_groups=run.failed_groups,
        pbs_sync_job_id=run.pbs_sync_job_id,
        pbs_remote_id=run.pbs_remote_id,
        pbs_task_upid=run.pbs_task_upid,
        elapsed_seconds=run.elapsed_seconds,
        auto_eject_after_success=run.auto_eject_after_success,
        mode=run.mode,
        created_at=run.created_at,
        **_run_extras(run, disk),
    )


def _run_extras(run, disk) -> dict[str, object]:  # noqa: ANN001
    from app.services.run_log import datastore_pair, run_kind

    running = run.status in (BackupRunStatus.PENDING, BackupRunStatus.RUNNING)
    return {
        **datastore_pair(run, disk),
        "kind": run_kind(run, disk),
        "cancellable": bool(running and run.pbs_task_upid and get_settings().pbs_mode == "api"),
    }
