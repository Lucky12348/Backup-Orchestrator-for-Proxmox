import logging
from datetime import datetime, timedelta

from sqlalchemy import and_, exists, or_, select
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import AgentHeartbeat, ExternalDisk
from app.schemas.agent import AgentDiskReportCreate, AgentHeartbeatCreate
from app.services import notifications
from app.services import disk_events, disk_guards
from app.services.disk_events import is_ejected
from app.services.host_agent import HostAgentError, get_host_agent_client, get_pbs_agent_client
from app.services.disk_identity import canonical_serial_number, serial_aliases, serials_match
from app.services.planning_scheduler import handle_disk_detected


logger = logging.getLogger(__name__)
# A disk plugged in again within 30 min is not detected again.
DISK_DETECTION_COOLDOWN_SECONDS = 1800
# B43: the run is ejecting this disk (detach, spin-down / power-off): it
# disappearing from the host is expected, not a removal without eject.
EJECT_RUN_STEPS = frozenset({"eject", "eject_flush", "auto_eject", "ejected", "release", "success"})
ZERO_SIZE_DISK_MESSAGE = "Disque détecté mais taille 0B — port/câble/initialisation USB probablement défaillant."


def has_agent_disks(db: Session) -> bool:
    statement = select(
        exists().where(
            ExternalDisk.source == "agent",
            ExternalDisk.serial_number.not_like("agent-report::%"),
            _preferred_disk_visibility_condition(),
        )
    )
    return bool(db.scalar(statement))


def list_preferred_disks(db: Session) -> list[ExternalDisk]:
    settings = get_settings()
    visibility_condition = _preferred_disk_visibility_condition()
    if settings.show_seed_disks:
        visibility_condition = or_(
            visibility_condition,
            and_(ExternalDisk.source == "seed", ExternalDisk.active.is_(True)),
        )
    statement = select(ExternalDisk).where(
        visibility_condition,
        ExternalDisk.serial_number.not_like("agent-report::%"),
    )
    if not settings.show_seed_disks:
        statement = statement.where(ExternalDisk.source != "seed")
    elif has_agent_disks(db):
        statement = statement.where(
            or_(
                ExternalDisk.source == "seed",
                ExternalDisk.source == "agent",
            )
        )

    return list(
        db.scalars(
            statement.order_by(
                ExternalDisk.trusted.desc(),
                ExternalDisk.connected.desc(),
                ExternalDisk.display_name.asc(),
            )
        )
    )


def record_agent_heartbeat(db: Session, payload: AgentHeartbeatCreate) -> AgentHeartbeat:
    heartbeat = AgentHeartbeat(
        hostname=payload.hostname,
        agent_version=payload.agent_version,
        observed_at=payload.observed_at.replace(tzinfo=None),
    )
    db.add(heartbeat)
    db.commit()
    db.refresh(heartbeat)
    return heartbeat


def ingest_agent_disk_report(db: Session, payload: AgentDiskReportCreate) -> list[ExternalDisk]:
    observed_at = payload.observed_at.replace(tzinfo=None)
    upserted: list[ExternalDisk] = []
    reported_aliases = {alias for item in payload.disks for alias in serial_aliases(item.serial_number)}
    detection_notifications: list[tuple[str, ExternalDisk]] = []
    planning_detections: list[ExternalDisk] = []
    # Live events (SSE), published after the commit.
    added_disks: list[ExternalDisk] = []
    removed_disks: list[ExternalDisk] = []
    updated_disks: list[ExternalDisk] = []
    before_snapshots: dict[int, tuple] = {}
    health_notifications: list[tuple[ExternalDisk, str, str | None]] = []
    pulled_disks: list[ExternalDisk] = []

    report_marker = db.scalar(
        select(ExternalDisk)
        .where(
            ExternalDisk.source == "agent",
            ExternalDisk.serial_number == f"agent-report::{payload.hostname}",
        )
    )
    if report_marker is None:
        report_marker = ExternalDisk(
            serial_number=f"agent-report::{payload.hostname}",
            display_name=f"Agent report marker {payload.hostname}",
            dedicated_backup_disk=True,
            allow_existing_data=False,
            source="agent",
            active=False,
            trusted=False,
        )
    report_marker.last_seen_at = observed_at
    report_marker.connected = False
    report_marker.reported_by_hostname = payload.hostname
    db.add(report_marker)

    stale_disks = list(
        db.scalars(
            select(ExternalDisk).where(
                ExternalDisk.source == "agent",
                ExternalDisk.active.is_(True),
                ExternalDisk.serial_number != f"agent-report::{payload.hostname}",
                or_(
                    ExternalDisk.reported_by_hostname == payload.hostname,
                    ExternalDisk.reported_by_hostname.is_(None),
                ),
            )
        )
    )

    for item in payload.disks:
        incoming_canonical = canonical_serial_number(item.serial_number)
        incoming_aliases = serial_aliases(item.serial_number)
        disk = _find_existing_disk_by_serial_identity(db, item.serial_number)
        unusable_zero_size = item.capacity_gb <= 0

        if disk is None:
            disk = ExternalDisk(
                serial_number=incoming_canonical or item.serial_number,
                dedicated_backup_disk=True,
                allow_existing_data=False,
                source="agent",
                active=True,
                trusted=item.trusted,
            )
            if unusable_zero_size:
                disk.dedicated_backup_disk = False
                disk.trusted = False
            previous_presence = "never_seen"
            is_new_disk = True
            logger.debug("disk classified as new because serial %s canonical %s was not found", item.serial_number, incoming_canonical)
        else:
            previous_presence = disk.presence_state or ("present" if disk.connected else "absent")
            is_new_disk = False
            if disk.id is not None:
                before_snapshots[disk.id] = disk_events.snapshot(disk)
            logger.debug(
                "disk classified as known because serial %s canonical %s matched disk id %s",
                item.serial_number,
                incoming_canonical,
                disk.id,
            )

        previous_serial = disk.serial_number
        disk.reported_serial_number = item.serial_number
        disk.reported_display_name = item.display_name
        disk.reported_model_name = item.model_name
        disk.reported_mount_path = item.mount_path
        disk.canonical_serial_number = incoming_canonical or disk.canonical_serial_number
        disk.serial_aliases = _merge_aliases(disk.serial_aliases, [*incoming_aliases, *serial_aliases(previous_serial)])
        if is_new_disk and (not disk.serial_number or serials_match(disk.serial_number, item.serial_number)):
            disk.serial_number = disk.canonical_serial_number or disk.serial_number or item.serial_number
        disk.display_name = _reconcile_display_name(disk.display_name, item.display_name)
        disk.model_name = _reconcile_model_name(disk.model_name, item.model_name)
        disk.capacity_gb = item.capacity_gb
        disk.filesystem_type = _reconcile_filesystem_type(disk, item)
        disk.mount_path = _reconcile_mount_path(disk, item)
        if _should_apply_host_filesystem_usage(disk, item):
            disk.filesystem_total_gb = item.filesystem_total_gb
            disk.filesystem_used_gb = item.filesystem_used_gb
            disk.filesystem_free_gb = item.filesystem_free_gb
        disk.detection_reason = ZERO_SIZE_DISK_MESSAGE if unusable_zero_size else item.detection_reason
        disk.candidate_type = "unusable" if unusable_zero_size else item.candidate_type
        if unusable_zero_size:
            disk.trusted = False
            disk.dedicated_backup_disk = False
            disk.allow_existing_data = False
        if (
            not is_new_disk
            and item.connected
            and _is_pbs_handoff_disk(disk)
            and disk_guards.active_run_for(db, disk.id) is None
        ):
            # Phase 5f: a disk handed to the PBS VM is invisible to the host;
            # seen again here = pulled out without eject (mounted on PBS).
            disk_guards.mark_unsafe_removal(db, disk, reason="mounted_on_pbs")
        if disk.handoff_status == "pulled" and item.connected:
            disk.handoff_status = None  # back on the host after a removal without eject
        if is_ejected(disk) and item.connected:
            # B25: still plugged in after a safe eject. It stays "ejected" (not
            # usable, no disk.added) until it is unplugged and plugged back.
            disk.connected = False
            disk.presence_state = "present"
        else:
            if is_ejected(disk):
                disk.handoff_status = None  # reported but gone: the eject is over
            disk.connected = True if _is_pbs_handoff_disk(disk) else item.connected
            disk.presence_state = "present" if disk.connected else "absent"
        disk.last_seen_at = observed_at
        if item.usb_location is not None:
            disk.usb_location = item.usb_location.model_dump()
            disk.usb_location_seen_at = observed_at
        if item.partitions is not None:
            disk.partitions = [part.model_dump() for part in item.partitions]
        _apply_hardware(disk, item, observed_at, health_notifications)
        if disk.connected and previous_presence == "absent" and disk.operator_decision == "ignored":
            disk.operator_decision = None  # "Ignore" lasts until the disk is plugged again
        disk.source = "agent"
        disk.reported_by_hostname = payload.hostname
        disk.active = True

        db.add(disk)
        upserted.append(disk)
        if disk.connected and previous_presence in {"never_seen", "absent"}:
            added_disks.append(disk)  # rejected ones too: the UI explains why
        elif disk.id is not None and before_snapshots.get(disk.id) != disk_events.snapshot(disk):
            updated_disks.append(disk)
        if disk.connected and previous_presence in {"never_seen", "absent"} and not _is_unusable_disk(disk) and not disk_events.is_hidden(disk):
            planning_detections.append(disk)
            if _disk_detection_cooldown_elapsed(disk, observed_at):
                detection_notifications.append(("new" if previous_presence == "never_seen" else "known", disk))
                disk.last_detection_notified_at = observed_at

    report_devices = None if payload.usb_devices is None else [device.model_dump() for device in payload.usb_devices]
    for disk in stale_disks:
        existing_aliases = set(serial_aliases(disk.serial_number)) | set(disk.serial_aliases or [])
        if existing_aliases & reported_aliases:
            continue

        if _is_pbs_handoff_disk(disk):
            # Phase 5f.2: handed to the PBS VM, so no block device here; its
            # USB device must still be present, or it was pulled out.
            from app.services.passthrough_watch import is_present

            if disk.pbs_handoff_slot and is_present(disk, report_devices) is False:
                pulled_disks.append(disk)
                continue
            disk.connected = True
            disk.presence_state = "present"
            disk.active = True
            disk.reported_by_hostname = payload.hostname
            disk.last_seen_at = observed_at
            db.add(disk)
            continue

        # B49: no block device. Its USB device may still be on the bus (spun
        # down / powered off by the eject): not usable, but not unplugged.
        from app.services.disk_presence import EJECTED, run_reached_datastore
        from app.services.passthrough_watch import is_present

        usb_there = is_present(disk, report_devices)
        active = disk_guards.active_run_for(db, disk.id) if disk.id is not None else None
        was_usable = bool(disk.connected) and not is_ejected(disk)
        # Only when the agent positively sees its USB device (no USB data: legacy B25 rule).
        stays_ejected = is_ejected(disk) and usb_there is True
        becomes_ejected = was_usable and usb_there is True and active is None
        if (disk.connected or disk.presence_state == "present") and not stays_ejected:
            removed_disks.append(disk)
            if (
                active is not None
                and not is_ejected(disk)
                and (active.current_step or "") not in EJECT_RUN_STEPS
                and run_reached_datastore(active)
            ):
                # Gone during a run AFTER the passthrough, datastore mounted: the
                # copy may be damaged. Before the passthrough nothing was written.
                disk_guards.mark_unsafe_removal(db, disk, reason="during_run")
        if stays_ejected or becomes_ejected:
            # Ejected until its USB device leaves the bus (unplugged) or the
            # operator reactivates it: an agent/app restart never makes it usable.
            disk.handoff_status = EJECTED
            disk.connected = False
            disk.presence_state = "present" if usb_there else "absent"
            disk.reported_by_hostname = payload.hostname
            db.add(disk)
            continue
        if is_ejected(disk):
            disk.handoff_status = None  # its USB device left the bus: unplugged after its eject
        disk.connected = False
        disk.presence_state = "absent"
        disk.active = False
        disk.reported_by_hostname = payload.hostname
        db.add(disk)

    disk_events.record_hotplug_history(db, payload.hostname, [item.model_dump() for item in payload.hotplug])
    db.commit()

    for disk in upserted:
        db.refresh(disk)
    for disk in removed_disks:
        db.refresh(disk)
    disk_events.publish_report_events(added_disks, removed_disks, updated_disks)

    for disk in pulled_disks:
        try:
            from app.services.passthrough_watch import handle_pulled

            handle_pulled(db, disk, reason="mounted_on_pbs")
        except Exception:
            logger.exception("Could not handle disk %s pulled out while handed to PBS", disk.id)

    for smart_disk, smart_status, detail in health_notifications:
        notifications.smart_degraded(smart_disk, smart_status, detail)

    for detection_type, disk in detection_notifications:
        try:
            if detection_type == "new":
                notifications.disk_new(disk)
        except Exception:
            continue

    for disk in planning_detections:
        try:
            handle_disk_detected(db, disk, observed_at)
        except Exception:
            continue

    return upserted


def _apply_hardware(
    disk: ExternalDisk,
    item,
    observed_at: datetime,
    health_notifications: list[tuple[ExternalDisk, str, str | None]],
) -> None:
    """Phase 5f: rotation/transport (disk kind), USB link speed and SMART."""
    if item.rotational is not None:
        disk.rotational = item.rotational
    if item.transport:
        disk.transport = item.transport
    if item.usb_location is not None:
        speed = item.usb_location.speed_mbps
        disk.usb_speed_mbps = int(speed) if speed else None
    smart = item.smart
    if smart is None:
        return
    previous = disk.smart_status
    disk.smart_status = smart.status
    disk.smart_details = smart.model_dump(mode="json", exclude={"status"})
    disk.smart_checked_at = smart.checked_at.replace(tzinfo=None) if smart.checked_at else observed_at
    if smart.status in {"warning", "danger"} and smart.status != previous:
        detail = ", ".join(smart.reasons) or smart.message
        health_notifications.append((disk, smart.status, detail))


def _find_existing_disk_by_serial_identity(db: Session, reported_serial: str) -> ExternalDisk | None:
    exact = db.scalar(
        select(ExternalDisk)
        .where(
            or_(
                ExternalDisk.serial_number == reported_serial,
                ExternalDisk.reported_serial_number == reported_serial,
            )
        )
        .limit(1)
    )
    if exact is not None:
        return exact

    canonical = canonical_serial_number(reported_serial)
    if canonical:
        by_canonical = db.scalar(
            select(ExternalDisk)
            .where(ExternalDisk.canonical_serial_number == canonical)
            .limit(1)
        )
        if by_canonical is not None:
            return by_canonical

    incoming_aliases = set(serial_aliases(reported_serial))
    for disk in db.scalars(select(ExternalDisk).where(ExternalDisk.serial_number.not_like("agent-report::%"))):
        existing_aliases = set(serial_aliases(disk.serial_number))
        if disk.reported_serial_number:
            existing_aliases.update(serial_aliases(disk.reported_serial_number))
        if disk.canonical_serial_number:
            existing_aliases.add(disk.canonical_serial_number)
        existing_aliases.update(disk.serial_aliases or [])
        if incoming_aliases & existing_aliases:
            return disk
    return None


def _reconcile_mount_path(disk: ExternalDisk, item) -> str | None:
    incoming = _normalize_optional_string(item.mount_path)
    existing = _normalize_optional_string(disk.mount_path)
    if incoming:
        return incoming
    if existing:
        return existing
    return incoming


def _reconcile_display_name(existing: str | None, incoming: str | None) -> str:
    existing_clean = _normalize_optional_string(existing)
    incoming_clean = _normalize_optional_string(incoming)
    if not existing_clean:
        return incoming_clean or "External disk"
    if not incoming_clean:
        return existing_clean
    if _is_bridge_name(incoming_clean) and not _is_bridge_name(existing_clean):
        return existing_clean
    return existing_clean


def _reconcile_model_name(existing: str | None, incoming: str | None) -> str | None:
    existing_clean = _normalize_optional_string(existing)
    incoming_clean = _normalize_optional_string(incoming)
    if not existing_clean:
        return incoming_clean
    if not incoming_clean:
        return existing_clean
    if _is_bridge_name(incoming_clean) and not _is_bridge_name(existing_clean):
        return existing_clean
    return existing_clean


def _is_bridge_name(value: str) -> bool:
    clean = value.strip().casefold()
    return clean in {"game drive", "external usb", "usb disk", "usb storage", "mass storage"}


def _merge_aliases(existing: list[str] | None, incoming: list[str]) -> list[str]:
    merged: list[str] = []
    for value in [*(existing or []), *incoming]:
        if value and value not in merged:
            merged.append(value)
    return merged


def _disk_matches_event_serial(disk: ExternalDisk, event_serial: str) -> bool:
    if serials_match(disk.serial_number, event_serial):
        return True
    if disk.reported_serial_number and serials_match(disk.reported_serial_number, event_serial):
        return True
    event_aliases = set(serial_aliases(event_serial))
    disk_aliases = set(disk.serial_aliases or [])
    if disk.canonical_serial_number:
        disk_aliases.add(disk.canonical_serial_number)
    return bool(event_aliases & disk_aliases)


def _reconcile_filesystem_type(disk: ExternalDisk, item) -> str | None:
    incoming = _normalize_optional_string(item.filesystem_type)
    existing = _normalize_optional_string(disk.filesystem_type)
    if incoming:
        return incoming
    if existing and item.connected:
        return existing
    return incoming


def _normalize_optional_string(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _is_pbs_handoff_disk(disk: ExternalDisk) -> bool:
    status = (disk.handoff_status or "").strip().casefold()
    return (
        status in {"attached_to_pbs", "visible_on_pbs"}
        or disk.pbs_visible is True
        or disk.pbs_handoff_slot is not None
    )


def _disk_detection_cooldown_elapsed(disk: ExternalDisk, observed_at: datetime) -> bool:
    previous = disk.last_detection_notified_at
    if previous is None:
        return True
    return (observed_at - previous).total_seconds() >= DISK_DETECTION_COOLDOWN_SECONDS


def _preferred_disk_visibility_condition():
    return or_(
        and_(ExternalDisk.source != "seed", ExternalDisk.active.is_(True)),
        and_(
            ExternalDisk.source != "seed",
            or_(
                ExternalDisk.trusted.is_(True),
                ExternalDisk.dedicated_backup_disk.is_(True),
                ExternalDisk.prepared_as_pbs_datastore.is_(True),
            ),
        ),
        ExternalDisk.pbs_visible.is_(True),
        ExternalDisk.pbs_handoff_slot.is_not(None),
        ExternalDisk.handoff_status.in_(["attached_to_pbs", "visible_on_pbs", "ejected"]),
    )


def _is_unusable_disk(disk: ExternalDisk) -> bool:
    return disk.capacity_gb <= 0 or disk.candidate_type == "unusable"


def refresh_disk_filesystem_usage_from_pbs(db: Session, disk: ExternalDisk) -> bool:
    if not disk.pbs_mount_path:
        return False
    client = get_pbs_agent_client()
    try:
        capabilities = set(client.get_version().get("capabilities") or [])
    except HostAgentError:
        return False
    if "filesystem-usage" not in capabilities:
        return False
    try:
        result = client.post("/filesystem-usage", {"mount_path": disk.pbs_mount_path})
    except HostAgentError:
        return False
    if not result.ok:
        return False
    _apply_filesystem_usage_payload(disk, result.payload)
    db.add(disk)
    db.commit()
    db.refresh(disk)
    return True


def apply_filesystem_usage_from_payload(db: Session, disk: ExternalDisk, payload: dict | None) -> None:
    if not isinstance(payload, dict):
        return
    _apply_filesystem_usage_payload(disk, payload)
    db.add(disk)
    db.commit()
    db.refresh(disk)


def _apply_filesystem_usage_payload(disk: ExternalDisk, payload: dict[str, object]) -> None:
    disk.filesystem_total_gb = _optional_int(payload.get("filesystem_total_gb"))
    disk.filesystem_used_gb = _optional_int(payload.get("filesystem_used_gb"))
    disk.filesystem_free_gb = _optional_int(payload.get("filesystem_free_gb"))
    # Phase 5f: usage history (graph) and 80 % / 90 % alerts.
    from app.services.disk_profile import record_usage

    record_usage(disk)


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _should_apply_host_filesystem_usage(disk: ExternalDisk, item) -> bool:
    if any(value is not None for value in (item.filesystem_total_gb, item.filesystem_used_gb, item.filesystem_free_gb)):
        return True
    return not bool(disk.pbs_visible)


def get_agent_status(db: Session) -> dict[str, datetime | str | bool | int | None]:
    settings = get_settings()
    latest_heartbeat = db.scalar(
        select(AgentHeartbeat).order_by(AgentHeartbeat.observed_at.desc()).limit(1)
    )
    last_report_at = db.scalar(
        select(ExternalDisk.last_seen_at)
        .where(ExternalDisk.source == "agent")
        .order_by(ExternalDisk.last_seen_at.desc())
        .limit(1)
    )

    now = datetime.utcnow()
    latest_seen = latest_heartbeat.observed_at if latest_heartbeat else None
    stale_after = settings.agent_stale_after_minutes
    threshold = now - timedelta(minutes=stale_after)
    last_seen_age_seconds = None
    if latest_seen is not None:
        last_seen_age_seconds = max(0, int((now - latest_seen).total_seconds()))

    if latest_seen is None:
        status = "disconnected"
        connected = False
    elif latest_seen >= threshold:
        status = "connected"
        connected = True
    else:
        status = "degraded"
        connected = False

    return {
        "connected": connected,
        "hostname": latest_heartbeat.hostname if latest_heartbeat else None,
        "last_heartbeat_at": latest_seen,
        "last_report_at": last_report_at,
        "status": status,
        "stale_after_minutes": stale_after,
        "last_seen_age_seconds": last_seen_age_seconds,
    }


# --- Disks page: approval flow and "Run detection again" (Phase 5b) ----------

DECISION_NAME_MISMATCH = "decision_name_mismatch"
DECISION_DISK_UNUSABLE = "decision_disk_unusable"
RESCAN_AGENT_MISSING = "rescan_agent_missing"
RESCAN_AGENT_OUTDATED = "rescan_agent_outdated"
RESCAN_FAILED = "rescan_failed"


def _coded_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


def apply_disk_decision(db: Session, disk_id: int, decision: str, confirm_name: str | None) -> ExternalDisk:
    disk = db.get(ExternalDisk, disk_id)
    if disk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Disk not found")
    if decision in {"approve", "approve_anyway"}:
        reason = disk_events.reject_reason(disk)
        # A disk reporting 0 bytes cannot hold anything; a too-small one can be
        # approved anyway, explicitly (the UI states the risk).
        if reason == "zero_size" or (reason == "too_small" and decision != "approve_anyway"):
            raise _coded_error(400, DECISION_DISK_UNUSABLE, "This disk cannot be used for backups.")
        if (confirm_name or "").strip() != (disk.display_name or "").strip():
            raise _coded_error(400, DECISION_NAME_MISMATCH, "Type the disk name exactly to approve it.")
        disk.trusted = True
        disk.dedicated_backup_disk = True
        disk.operator_decision = disk_events.APPROVED_SMALL if reason == "too_small" else None
    elif decision == "ignore":
        disk.operator_decision = "ignored"
    elif decision == "reject":
        disk.trusted = False
        disk.operator_decision = "rejected"
    elif decision == "hide":
        disk.trusted = False
        disk.operator_decision = disk_events.HIDDEN
    else:  # clear
        disk.operator_decision = None
    db.add(disk)
    db.commit()
    db.refresh(disk)
    disk_events.publish_report_events([], [], [disk])
    return disk


def rescan_disks(db: Session) -> None:
    settings = get_settings()
    if not settings.host_agent_base_url:
        raise _coded_error(409, RESCAN_AGENT_MISSING, "No host agent is installed.")
    try:
        get_host_agent_client().post("/disk/rescan", {})
    except HostAgentError as exc:
        if exc.status_code in {403, 404, 405}:
            raise _coded_error(409, RESCAN_AGENT_OUTDATED, "Update the host agent to run detection from the UI.") from None
        raise _coded_error(502, RESCAN_FAILED, str(exc)) from None
