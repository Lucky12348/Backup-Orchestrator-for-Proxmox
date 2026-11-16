import logging
import re
from datetime import date, datetime, time, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from app.api.dependencies import DbSession
from app.models import (
    ExternalDisk,
    ScheduledBackupEvent,
    ScheduledBackupRun,
    ScheduledBackupRunStatus,
)
from app.schemas import (
    DiskPlanningRead,
    PlanningOverviewRead,
    ScheduledBackupCalendarOccurrenceRead,
    ScheduledBackupEventCreate,
    ScheduledBackupEventRead,
    ScheduledBackupEventUpdate,
    ScheduledBackupRunRead,
    UnplannedAssetRead,
)
from app.services.disk_eject import is_disk_auto_eject_eligible
from app.services.disk_identity import (
    disk_matches_serial,
    serial_aliases,
    serials_match,
)
from app.services.planning import (
    get_disk_planning,
    get_planning_overview,
    get_unplanned_assets,
)
from app.services.planning_scheduler import (
    cancel_planning_run,
    confirm_planning_run,
    expand_occurrences,
    next_occurrence,
    run_disk_serial,
    run_event_now,
)

router = APIRouter(prefix="/planning", tags=["planning"])
logger = logging.getLogger(__name__)
ACTIVE_RUN_STATUSES = {
    ScheduledBackupRunStatus.PENDING,
    ScheduledBackupRunStatus.WAITING_FOR_DISK,
    ScheduledBackupRunStatus.WAITING_FOR_CONFIRMATION,
    ScheduledBackupRunStatus.RUNNING,
}
AUTO_EJECT_UNSUPPORTED_DETAIL = "L'auto-eject planifie est disponible uniquement pour les disques dedies PBS."


@router.get("/disks", response_model=list[DiskPlanningRead])
def get_planning_disks(db: DbSession) -> list[DiskPlanningRead]:
    return [DiskPlanningRead(**summary.__dict__) for summary in get_disk_planning(db)]


@router.get("/unplanned-assets", response_model=list[UnplannedAssetRead])
def get_unplanned(db: DbSession) -> list[UnplannedAssetRead]:
    return [
        UnplannedAssetRead(
            vm_id=vm.id,
            name=vm.name,
            vm_type=vm.vm_type,
            size_gb=vm.size_gb,
            critical=vm.critical,
        )
        for vm in get_unplanned_assets(db)
    ]


@router.get("/overview", response_model=PlanningOverviewRead)
def get_overview(db: DbSession) -> PlanningOverviewRead:
    return PlanningOverviewRead(**get_planning_overview(db).__dict__)


@router.get("/events", response_model=list[ScheduledBackupEventRead])
def list_events(db: DbSession) -> list[ScheduledBackupEventRead]:
    events = list(
        db.scalars(
            select(ScheduledBackupEvent)
            .where(ScheduledBackupEvent.deleted_at.is_(None))
            .order_by(ScheduledBackupEvent.window_starts_at.asc())
        )
    )
    return [_event_read(db, event) for event in events]


@router.get("/calendar", response_model=list[ScheduledBackupCalendarOccurrenceRead])
def get_calendar_occurrences(
    db: DbSession,
    start: date = Query(..., description="Visible range start date, inclusive"),
    end: date = Query(..., description="Visible range end date, inclusive"),
) -> list[ScheduledBackupCalendarOccurrenceRead]:
    if end < start:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="end must be on or after start")
    if (end - start).days > 370:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="calendar range cannot exceed 370 days")

    range_start = datetime.combine(start, time.min)
    range_end = datetime.combine(end, time.max)
    events = list(
        db.scalars(
            select(ScheduledBackupEvent)
            .where(ScheduledBackupEvent.deleted_at.is_(None))
            .order_by(ScheduledBackupEvent.window_starts_at.asc())
        )
    )
    runs = list(
        db.scalars(
            select(ScheduledBackupRun).where(
                ScheduledBackupRun.window_starts_at <= range_end,
                ScheduledBackupRun.window_ends_at >= range_start,
            )
        )
    )
    run_by_event_and_schedule = {(run.event_id, run.scheduled_for): run for run in runs}
    occurrences: list[ScheduledBackupCalendarOccurrenceRead] = []
    for event in events:
        if not event.enabled:
            continue
        for scheduled_for in expand_occurrences(event, range_start, range_end):
            run = run_by_event_and_schedule.get((event.id, scheduled_for))
            window_ends_at = scheduled_for + timedelta(minutes=event.window_duration_minutes)
            occurrences.append(
                ScheduledBackupCalendarOccurrenceRead(
                    event_id=event.id,
                    occurrence_id=f"{event.id}:{scheduled_for.isoformat()}",
                    scheduled_for=scheduled_for,
                    title=event.title,
                    disk_serial=event.disk_serial,
                    disk_label=event.disk_label_or_model,
                    window_starts_at=scheduled_for,
                    window_ends_at=window_ends_at,
                    status=run.status if run else None,
                    run_id=run.id if run else None,
                    start_mode=event.start_mode,
                    auto_eject_after_success=event.auto_eject_after_success,
                )
            )
    logger.info(
        "planning calendar expanded count=%s range_start=%s range_end=%s",
        len(occurrences),
        range_start.isoformat(timespec="seconds"),
        range_end.isoformat(timespec="seconds"),
    )
    return sorted(occurrences, key=lambda item: item.window_starts_at)


def _event_timezone(db: DbSession, value: str | None) -> str:
    """Explicit zone (validated) or the app zone. Existing events keep theirs."""
    from app.core.timezones import InvalidTimezoneError, validate_timezone
    from app.services.settings_service import effective_settings

    try:
        return validate_timezone(value or effective_settings(db).timezone)
    except InvalidTimezoneError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


VM_GROUP_RE = re.compile(r"^(vm|ct)/\d{1,9}$")


def _coded(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


def _normalize_planning(values: dict, current: ScheduledBackupEvent | None = None) -> dict:
    """Rotation (first disk = disk_serial), VM/CT groups and the interval."""
    if "rotation_disk_serials" in values:
        serials: list[str] = []
        for item in values["rotation_disk_serials"] or []:
            serial = str(item).strip()
            if serial and serial not in serials:
                serials.append(serial)
        values["rotation_disk_serials"] = serials or None
        if serials:
            values["disk_serial"] = serials[0]
    if "vm_groups" in values:
        groups = sorted({str(item).strip().lower() for item in values["vm_groups"] or [] if str(item).strip()})
        bad = [group for group in groups if not VM_GROUP_RE.match(group)]
        if bad:
            raise _coded(422, "planning_vm_group_invalid", f"Invalid VM/CT group: {', '.join(bad)} (expected vm/<id> or ct/<id>).")
        values["vm_groups"] = groups or None
    if "recurrence_config" in values and values["recurrence_config"] is not None:
        config = dict(values["recurrence_config"])
        try:
            interval = int(config.get("interval") or 1)
        except (TypeError, ValueError):
            interval = 0
        if not 1 <= interval <= 52:
            raise _coded(422, "planning_interval_invalid", "The interval must be between 1 and 52.")
        config["interval"] = interval
        values["recurrence_config"] = config
    if not (values.get("disk_serial") or (current.disk_serial if current else "")):
        raise _coded(422, "planning_disk_required", "Choose at least one disk.")
    return values


def _all_serials(values: dict, current: ScheduledBackupEvent | None = None) -> list[str]:
    rotation = values.get("rotation_disk_serials", current.rotation_disk_serials if current else None) or []
    first = values.get("disk_serial", current.disk_serial if current else None)
    return list(dict.fromkeys([*([first] if first else []), *rotation]))


@router.post("/events", response_model=ScheduledBackupEventRead)
def create_event(payload: ScheduledBackupEventCreate, db: DbSession) -> ScheduledBackupEventRead:
    data = _normalize_planning(payload.model_dump())
    for serial in _all_serials(data):
        _validate_auto_eject_configuration(db, serial, data["auto_eject_after_success"])
    now = datetime.utcnow()
    data["timezone"] = _event_timezone(db, data.get("timezone"))
    event = ScheduledBackupEvent(
        **data,
        created_at=now,
        updated_at=now,
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return _event_read(db, event)


@router.get("/events/{event_id}", response_model=ScheduledBackupEventRead)
def get_event(event_id: int, db: DbSession) -> ScheduledBackupEventRead:
    event = db.get(ScheduledBackupEvent, event_id)
    if event is None or event.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scheduled event not found")
    return _event_read(db, event)


@router.patch("/events/{event_id}", response_model=ScheduledBackupEventRead)
def update_event(event_id: int, payload: ScheduledBackupEventUpdate, db: DbSession) -> ScheduledBackupEventRead:
    event = db.get(ScheduledBackupEvent, event_id)
    if event is None or event.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scheduled event not found")
    values = _normalize_planning(payload.model_dump(exclude_unset=True), event)
    if "timezone" in values:
        values["timezone"] = _event_timezone(db, values["timezone"])
    for serial in _all_serials(values, event):
        _validate_auto_eject_configuration(
            db, serial, values.get("auto_eject_after_success", event.auto_eject_after_success)
        )
    for field, value in values.items():
        setattr(event, field, value)
    event.updated_at = datetime.utcnow()
    db.add(event)
    db.commit()
    db.refresh(event)
    return _event_read(db, event)


@router.delete("/events/{event_id}", status_code=204)
def delete_event(event_id: int, db: DbSession) -> None:
    event = db.get(ScheduledBackupEvent, event_id)
    if event is None or event.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scheduled event not found")
    active_run = db.scalar(
        select(ScheduledBackupRun.id)
        .where(
            ScheduledBackupRun.event_id == event.id,
            ScheduledBackupRun.status.in_(list(ACTIVE_RUN_STATUSES)),
        )
        .limit(1)
    )
    if active_run is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot delete event while a run is active. Cancel the run first.",
        )
    event.enabled = False
    event.deleted_at = datetime.utcnow()
    event.updated_at = event.deleted_at
    db.add(event)
    db.commit()
    logger.info("planning event soft-deleted event_id=%s", event.id)


@router.get("/agenda")
def get_agenda(
    db: DbSession,
    start: datetime = Query(..., description="Visible range start (ISO, UTC)"),
    end: datetime = Query(..., description="Visible range end (ISO, UTC)"),
    proxmox: bool = Query(default=False, description="Include the Proxmox VE backup jobs (read only)"),
) -> dict:
    """Calendar items (planned occurrences, manual runs, Proxmox jobs) — Phase 6."""
    from app.services.agenda import build_agenda
    from app.services.settings_service import effective_settings

    start_utc = start.astimezone(timezone.utc).replace(tzinfo=None) if start.tzinfo else start
    end_utc = end.astimezone(timezone.utc).replace(tzinfo=None) if end.tzinfo else end
    if end_utc < start_utc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="end must be on or after start")
    if (end_utc - start_utc).days > 400:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="agenda range cannot exceed 400 days")
    return build_agenda(db, start_utc, end_utc, include_proxmox=proxmox, zone_name=effective_settings(db).timezone)


@router.get("/runs", response_model=list[ScheduledBackupRunRead])
def list_runs(db: DbSession) -> list[ScheduledBackupRunRead]:
    runs = list(db.scalars(select(ScheduledBackupRun).order_by(ScheduledBackupRun.window_starts_at.desc()).limit(100)))
    return [_run_read(db, run) for run in runs]


@router.post("/events/{event_id}/run-now", response_model=ScheduledBackupRunRead)
def run_now(event_id: int, db: DbSession) -> ScheduledBackupRunRead:
    try:
        run = run_event_now(db, event_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return _run_read(db, run)


@router.post("/runs/{run_id}/confirm", response_model=ScheduledBackupRunRead)
def confirm_run(run_id: int, db: DbSession) -> ScheduledBackupRunRead:
    try:
        run = confirm_planning_run(db, run_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _run_read(db, run)


@router.post("/runs/{run_id}/cancel", response_model=ScheduledBackupRunRead)
def cancel_run(run_id: int, db: DbSession) -> ScheduledBackupRunRead:
    try:
        run = cancel_planning_run(db, run_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return _run_read(db, run)


def _event_read(db, event: ScheduledBackupEvent) -> ScheduledBackupEventRead:
    now = datetime.utcnow()
    active_run = db.scalar(
        select(ScheduledBackupRun)
        .where(
            ScheduledBackupRun.event_id == event.id,
            ScheduledBackupRun.status.in_(
                [
                    *ACTIVE_RUN_STATUSES,
                ]
            ),
        )
        .order_by(ScheduledBackupRun.window_starts_at.asc())
        .limit(1)
    )
    return ScheduledBackupEventRead(
        **{
            **event.__dict__,
            "next_occurrence_at": next_occurrence(event, now),
            "active_run": _run_read(db, active_run) if active_run is not None else None,
        }
    )


def _run_read(db, run: ScheduledBackupRun) -> ScheduledBackupRunRead:
    event = db.get(ScheduledBackupEvent, run.event_id)
    return ScheduledBackupRunRead(
        **{
            **run.__dict__,
            "event_title": event.title if event else None,
            "disk_serial": (run_disk_serial(event, run) if event else run.disk_serial),
        }
    )


def _validate_auto_eject_configuration(db, disk_serial: str, auto_eject_after_success: bool) -> None:
    if not auto_eject_after_success:
        return
    disk = _find_disk_by_event_serial(db, disk_serial)
    if disk is None or not is_disk_auto_eject_eligible(disk):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=AUTO_EJECT_UNSUPPORTED_DETAIL)


def _find_disk_by_event_serial(db, event_serial: str) -> ExternalDisk | None:
    exact = db.scalar(select(ExternalDisk).where(ExternalDisk.serial_number == event_serial).limit(1))
    if exact is not None:
        return exact

    event_aliases = set(serial_aliases(event_serial))
    for disk in db.scalars(select(ExternalDisk)):
        if disk_matches_serial(disk, event_serial):
            return disk
        if serials_match(disk.serial_number, event_serial):
            return disk
        if disk.reported_serial_number and serials_match(disk.reported_serial_number, event_serial):
            return disk
        disk_aliases = set(disk.serial_aliases or [])
        if disk.canonical_serial_number:
            disk_aliases.add(disk.canonical_serial_number)
        if event_aliases & disk_aliases:
            return disk
    return None
