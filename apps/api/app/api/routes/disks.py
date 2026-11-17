from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select

from app.api.dependencies import DbSession
from app.models import ExternalDisk
from app.schemas import (
    DiskHandoffRequest,
    DiskHandoffStatusRead,
    DiskPreparationRequest,
    DiskPreparationRunRead,
    ExternalDiskRead,
    ExternalDiskUpdate,
)
from app.schemas.external_disk import ExternalDiskDecision
from app.services.disk_handoff import (
    detach_disk_from_pbs,
    get_pbs_disk_visibility,
    handoff_disk_to_pbs,
)
from app.services.disk_eject import eject_dedicated_external_disk
from app.services.activity_cleanup import cleanup_disk_preparation_runs
from app.services.disk_preparations import get_disk_preparation_run, list_disk_preparation_runs, prepare_disk
from app.services.disks import apply_disk_decision, list_preferred_disks, rescan_disks


router = APIRouter(prefix="/disks", tags=["external-disks"])


@router.get("", response_model=list[ExternalDiskRead])
def list_disks(db: DbSession) -> list[ExternalDisk]:
    return list(db.scalars(select(ExternalDisk).order_by(ExternalDisk.display_name.asc())))


@router.get("/preferred", response_model=list[ExternalDiskRead])
def get_preferred_disks(db: DbSession) -> list[ExternalDisk]:
    return list_preferred_disks(db)


@router.get("/alerts")
def disk_alerts_route(db: DbSession) -> list[dict]:
    """Phase 5f guards, decided server-side: usage 80/90 %, SMART, removal
    without eject, USB 2.0, forgotten rotation."""
    from app.services.disk_guards import disk_alerts

    return disk_alerts(db)


@router.post("/rescan")
def rescan_disks_route(db: DbSession) -> dict[str, str]:
    """Disks page "Run detection again": the host agent sends a report now
    (the disks then arrive over SSE like a hot-plug)."""
    rescan_disks(db)
    return {"status": "ok"}


@router.post("/{disk_id}/decision", response_model=ExternalDiskRead)
def disk_decision_route(disk_id: int, payload: ExternalDiskDecision, db: DbSession) -> ExternalDisk:
    """Approval flow of a detected disk: approve (name typed back), ignore
    (until plugged again), always reject, or clear the decision."""
    return apply_disk_decision(db, disk_id, payload.decision, payload.confirm_name)


@router.patch("/{disk_id}", response_model=ExternalDiskRead)
def update_disk(
    disk_id: int,
    payload: ExternalDiskUpdate,
    db: DbSession,
) -> ExternalDisk:
    disk = db.get(ExternalDisk, disk_id)
    if disk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Disk not found")

    values = payload.model_dump(exclude_unset=True)
    if "display_name" in values:
        name = (values["display_name"] or "").strip()
        if not name:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={"code": "disk_name_required", "message": "The disk name cannot be empty."})
        values["display_name"] = name
    if "location_note" in values:
        values["location_note"] = (values["location_note"] or "").strip() or None
    if "retention" in values:
        from app.services.disk_profile import RetentionError, normalize_retention

        try:
            values["retention"] = normalize_retention(values["retention"])
        except RetentionError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={"code": "retention_invalid", "message": str(exc)}) from exc
    if (disk.capacity_gb <= 0 or disk.candidate_type == "unusable") and (
        values.get("trusted") is True or values.get("dedicated_backup_disk") is True
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Disque détecté mais taille 0B — port/câble/initialisation USB probablement défaillant.",
        )

    for field, value in values.items():
        setattr(disk, field, value)

    db.add(disk)
    db.commit()
    db.refresh(disk)
    return disk


@router.post("/{disk_id}/prepare", response_model=DiskPreparationRunRead)
def prepare_disk_route(
    disk_id: int,
    payload: DiskPreparationRequest,
    db: DbSession,
) -> DiskPreparationRunRead:
    run = prepare_disk(
        db,
        disk_id=disk_id,
        mode=payload.mode,
        mount_base_path=payload.mount_base_path,
        confirm_destructive=payload.confirm_destructive,
    )
    return DiskPreparationRunRead.model_validate(run)


@router.get("/{disk_id}/preparation-runs", response_model=list[DiskPreparationRunRead])
def get_preparation_runs(disk_id: int, db: DbSession) -> list[DiskPreparationRunRead]:
    get_disk = db.get(ExternalDisk, disk_id)
    if get_disk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Disk not found")

    return [
        DiskPreparationRunRead.model_validate(run)
        for run in list_disk_preparation_runs(db, disk_id)
    ]


@router.delete("/preparation-runs/cleanup")
def cleanup_preparation_runs(db: DbSession, keep_last: int = 10) -> dict[str, int]:
    deleted = cleanup_disk_preparation_runs(db, keep_last=keep_last)
    return {"deleted": deleted, "keep_last": keep_last}


@router.get("/preparation-runs/{run_id}", response_model=DiskPreparationRunRead)
def get_preparation_run(run_id: int, db: DbSession) -> DiskPreparationRunRead:
    run = get_disk_preparation_run(db, run_id)
    return DiskPreparationRunRead.model_validate(run)


@router.post("/{disk_id}/handoff-to-pbs", response_model=DiskHandoffStatusRead)
def handoff_disk_to_pbs_route(
    disk_id: int,
    payload: DiskHandoffRequest,
    db: DbSession,
) -> DiskHandoffStatusRead:
    disk = db.get(ExternalDisk, disk_id)
    if disk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Disk not found")
    result = handoff_disk_to_pbs(db, disk, confirmation=payload.confirmation)
    return DiskHandoffStatusRead.model_validate(result)


@router.delete("/{disk_id}/handoff-to-pbs", response_model=DiskHandoffStatusRead)
def detach_disk_from_pbs_route(disk_id: int, db: DbSession) -> DiskHandoffStatusRead:
    disk = db.get(ExternalDisk, disk_id)
    if disk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Disk not found")
    result = detach_disk_from_pbs(db, disk)
    return DiskHandoffStatusRead.model_validate(result)


@router.post("/{disk_id}/eject", response_model=ExternalDiskRead)
def eject_disk_route(disk_id: int, db: DbSession) -> ExternalDiskRead:
    disk = eject_dedicated_external_disk(db, disk_id)
    return ExternalDiskRead.model_validate(disk)


@router.get("/{disk_id}/pbs-visibility", response_model=DiskHandoffStatusRead)
def get_pbs_visibility_route(disk_id: int, db: DbSession) -> DiskHandoffStatusRead:
    disk = db.get(ExternalDisk, disk_id)
    if disk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Disk not found")
    result = get_pbs_disk_visibility(db, disk)
    return DiskHandoffStatusRead.model_validate(result)


# --- Phase 5f: disk sheet ---------------------------------------------------------------------------
def _disk_or_404(db, disk_id: int) -> ExternalDisk:
    disk = db.get(ExternalDisk, disk_id)
    if disk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Disk not found")
    return disk


@router.get("/{disk_id}/contents")
def disk_contents_route(disk_id: int, db: DbSession) -> dict:
    """VM/CT on the disk (cached from PBS, readable while unplugged)."""
    from app.services.disk_contents import contents_payload

    return contents_payload(db, _disk_or_404(db, disk_id))


@router.post("/{disk_id}/contents/refresh")
def refresh_disk_contents_route(disk_id: int, db: DbSession) -> dict:
    """Reads the disk's snapshots in PBS now (its datastore must be mounted)."""
    from app.services.disk_contents import contents_payload, store_contents
    from app.services.host_agent import HostAgentError, get_pbs_agent_client

    disk = _disk_or_404(db, disk_id)
    if not disk.pbs_datastore_name:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"code": "contents_no_datastore", "message": "This disk holds no PBO datastore."})
    client = get_pbs_agent_client()
    try:
        result = client.post("/list-datastore-snapshots", {"datastore_name": disk.pbs_datastore_name})
    except HostAgentError as exc:
        code = (exc.payload or {}).get("error_code") if isinstance(exc.payload, dict) else None
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "contents_not_mounted" if code == "datastore_not_mounted" else "contents_unavailable", "message": str(exc)},
        ) from exc
    store_contents(db, disk, (result.payload or {}).get("snapshots") or [])
    db.commit()
    db.refresh(disk)
    return contents_payload(db, disk)


@router.put("/{disk_id}/photo", response_model=ExternalDiskRead)
async def upload_disk_photo(disk_id: int, db: DbSession, file: UploadFile = File(...)) -> ExternalDisk:
    from app.services.disk_photos import MAX_UPLOAD_BYTES, save_photo

    disk = _disk_or_404(db, disk_id)
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    save_photo(disk, data)
    db.add(disk)
    db.commit()
    db.refresh(disk)
    return disk


@router.get("/{disk_id}/photo")
def get_disk_photo(disk_id: int, db: DbSession) -> FileResponse:
    from app.services.disk_photos import photo_path

    path = photo_path(_disk_or_404(db, disk_id))
    if path is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No photo")
    return FileResponse(path, media_type="image/webp", headers={"Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff"})


@router.delete("/{disk_id}/photo", response_model=ExternalDiskRead)
def delete_disk_photo(disk_id: int, db: DbSession) -> ExternalDisk:
    from app.services.disk_photos import remove_photo_file

    disk = _disk_or_404(db, disk_id)
    remove_photo_file(disk)
    db.add(disk)
    db.commit()
    db.refresh(disk)
    return disk


class DiskVerifyRequest(BaseModel):
    confirmation: bool = False
    # B45: a verification releases the disk (it stays connected); eject on request.
    auto_eject_after_success: bool = False


@router.post("/{disk_id}/verify")
def verify_disk_route(disk_id: int, payload: DiskVerifyRequest, background_tasks: BackgroundTasks, db: DbSession) -> dict:
    """Verify-only run (after a removal without eject): hand-off, reuse the
    datastore (never format), PBS verify of every snapshot, eject."""
    from app.services.external_backups import execute_external_backup_run, run_external_backup

    _disk_or_404(db, disk_id)
    run = run_external_backup(
        db,
        disk_id,
        payload.confirmation,
        auto_eject_after_success=payload.auto_eject_after_success,
        verify_only=True,
    )
    background_tasks.add_task(execute_external_backup_run, run.id)
    return {"run_id": run.id, "status": run.status.value}


@router.post("/{disk_id}/verify/dismiss", response_model=ExternalDiskRead)
def dismiss_verify_route(disk_id: int, db: DbSession) -> ExternalDisk:
    """The operator chose not to verify: clear the recommendation."""
    from app.services.disk_guards import clear_verify_recommended

    disk = _disk_or_404(db, disk_id)
    clear_verify_recommended(disk)
    db.add(disk)
    db.commit()
    db.refresh(disk)
    return disk
