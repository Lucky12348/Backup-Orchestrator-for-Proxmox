"""Is the disk really usable right now? (B49)

"USB device present" (sysfs, `usb_devices` of the host reports) is not "disk
usable" (a block device with the disk's serial). After an eject the disk is
spun down / powered off: its USB device may stay while the block device is
gone. Such a disk stays "Ejected" (services/disks.py) until its USB device
leaves the bus (unplugged) or the operator reactivates it (`reactivate`: the
host agent re-enumerates the USB device, agent/usb_reset.py).

Before a run is created (button or planning), `live_check` asks the host
agent where the disk is NOW: absent = no run, coded 409.

Verification required only when the copy may be damaged: the disk was
removed (or the run failed) AFTER the passthrough, with the datastore
mounted (`run_reached_datastore`); a failure before it changes nothing.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ActivityEvent, ExternalBackupRun, ExternalDisk

logger = logging.getLogger(__name__)

EJECTED = "ejected"
# Steps where the disk is handed to PBS and its datastore mounted (or later).
MOUNTED_STEPS = frozenset(
    {
        "prepare_disk",
        "prepare_dedicated_datastore",
        "prepare_external_datastore",
        "destructive_format",
        "create_datastore",
        "mount_datastore",
        "run_export",
        "sync_job",
        "sync",
        "export",
        "cleanup",
        "verify",
        "repair",
    }
)
_MOUNTED_LINE = re.compile(rf"^\[[^\]]+\] ({'|'.join(sorted(MOUNTED_STEPS))})\b", re.MULTILINE)

REACTIVATE_NOT_EJECTED = "reactivate_not_ejected"
REACTIVATE_UNKNOWN_PORT = "reactivate_unknown_port"
REACTIVATE_AGENT_OUTDATED = "reactivate_agent_outdated"
REACTIVATE_FAILED = "reactivate_failed"
REACTIVATE_RUN_ACTIVE = "reactivate_run_active"


def run_reached_datastore(run: ExternalBackupRun | None) -> bool:
    """The run handed the disk to PBS and got to its datastore."""
    if run is None:
        return False
    if (run.current_step or "") in MOUNTED_STEPS:
        return True
    return bool(_MOUNTED_LINE.search(run.stdout_log or ""))


def _coded(status_code: int, code: str, message: str, **extra: Any) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message, **extra})


def _host_client(client: Any | None) -> Any | None:
    if client is not None:
        return client
    from app.core.config import get_settings

    if not get_settings().host_agent_base_url:
        return None
    from app.services.host_agent import get_host_agent_client

    return get_host_agent_client()


def _usb_present(client: Any, disk: ExternalDisk) -> bool | None:
    from app.services.passthrough_watch import _host_devices, is_present

    return is_present(disk, _host_devices(client))


def live_check(db: Session, disk: ExternalDisk, *, client: Any | None = None) -> dict[str, Any] | None:
    """{"present": bool, "usb_present": bool | None} asked to the host agent
    now; None when it cannot tell (no agent, unreachable: the hand-off will)."""
    client = _host_client(client)
    if client is None:
        return None
    try:
        answer = client.post("/disk/usb-location", {"serial": disk.serial_number}).payload or {}
    except Exception as exc:  # noqa: BLE001
        logger.info("Live presence check of %s unavailable: %s", disk.serial_number, exc)
        return None
    if answer.get("found"):
        return {"present": True, "usb_present": True}
    return {"present": False, "usb_present": _usb_present(client, disk)}


def mark_not_usable(db: Session, disk: ExternalDisk, usb_present: bool | None) -> None:
    """Live check said absent: Ejected when its USB device is still there
    (reactivable), unplugged otherwise. Published to the UI."""
    from app.services import disk_events

    if usb_present:
        disk.handoff_status = EJECTED
        disk.presence_state = "present"
    else:
        disk.presence_state = "absent"
    disk.connected = False
    db.add(disk)
    db.commit()
    db.refresh(disk)
    disk_events.publish_report_events([], [], [disk])


def require_present(db: Session, disk: ExternalDisk, *, client: Any | None = None) -> None:
    """Right before a run is created: refuses (409, no run) a disk that is not
    there now. `disk_not_present_reactivable` when its USB device still is."""
    verdict = live_check(db, disk, client=client)
    if verdict is None or verdict["present"]:
        return
    reactivable = bool(verdict["usb_present"])
    mark_not_usable(db, disk, verdict["usb_present"])
    code = "disk_not_present_reactivable" if reactivable else "disk_not_present"
    message = (
        "The disk is not usable on the Proxmox host (ejected or powered off): reactivate it, or unplug it and plug it back in."
        if reactivable
        else "The disk is not plugged into the Proxmox host."
    )
    raise _coded(status.HTTP_409_CONFLICT, code, message, reasons=[{"code": code, "message": message, "reactivable": reactivable}])


def reactivate(db: Session, disk: ExternalDisk, *, client: Any | None = None) -> ExternalDisk:
    """"Reactivate" on an ejected disk: the host agent re-enumerates its USB
    device and waits for the block device (agent/usb_reset.py)."""
    from app.services import disk_events, disk_guards
    from app.services.host_agent import HostAgentError
    from app.services.passthrough_watch import disk_port

    if (disk.handoff_status or "").casefold() != EJECTED:
        raise _coded(409, REACTIVATE_NOT_EJECTED, "Only an ejected disk can be reactivated.")
    if disk_guards.active_run_for(db, disk.id) is not None:
        raise _coded(409, REACTIVATE_RUN_ACTIVE, "A run is using this disk.")
    port = disk_port(disk)
    if port is None:
        raise _coded(409, REACTIVATE_UNKNOWN_PORT, "The USB port of this disk is unknown: unplug it and plug it back in.")
    client = _host_client(client)
    if client is None:
        raise _coded(409, REACTIVATE_AGENT_OUTDATED, "No host agent is installed.")
    location = disk.usb_location if isinstance(disk.usb_location, dict) else {}
    payload = {
        "serial": disk.serial_number,
        "port_id": port,
        "vendor_id": location.get("vendor_id"),
        "product_id": location.get("product_id"),
        "usb_serial": location.get("serial"),
    }
    try:
        result = client.post("/disk/reactivate", {key: value for key, value in payload.items() if value})
    except HostAgentError as exc:
        if exc.status_code in {403, 404, 405}:
            raise _coded(409, REACTIVATE_AGENT_OUTDATED, "The host agent is too old to reactivate a disk.") from None
        raise _coded(502, REACTIVATE_FAILED, f"Reactivation failed: {exc}. Unplug the disk and plug it back in.") from None
    answer = result.payload or {}
    if not answer.get("ok", result.ok):
        raise _coded(
            409,
            REACTIVATE_FAILED,
            f"{answer.get('message') or 'The disk did not come back.'} Unplug the disk and plug it back in.",
            agent_code=answer.get("error_code"),
        )
    disk.handoff_status = None
    disk.connected = True
    disk.presence_state = "present"
    disk.last_seen_at = datetime.utcnow()
    db.add(disk)
    db.add(
        ActivityEvent(
            created_at=datetime.utcnow(),
            category="disk",
            action="reactivated",
            status="success",
            summary=f"{disk.display_name} ({disk.serial_number}) reactivated without unplugging it ({answer.get('method') or 'already there'}).",
            details={"disk_id": disk.id, "serial": disk.serial_number, "method": answer.get("method"), "device": answer.get("device")},
        )
    )
    db.commit()
    db.refresh(disk)
    disk_events.publish_report_events([disk], [], [])
    return disk


def clear_mistaken_verify_flags(db: Session) -> int:
    """One-off repair (B49, at startup): "verification recommended" set by a
    disk seen absent during a run that failed BEFORE the passthrough (before
    this fix every such run flagged the disk). Idempotent."""
    cleared = 0
    for disk in db.scalars(select(ExternalDisk).where(ExternalDisk.verify_recommended.is_(True))):
        if disk.verify_required:
            continue
        events = [
            event
            for event in db.scalars(
                select(ActivityEvent)
                .where(ActivityEvent.category == "disk", ActivityEvent.action == "unsafe_removal")
                .order_by(ActivityEvent.created_at.desc())
            )
            if isinstance(event.details, dict) and event.details.get("disk_id") == disk.id
        ]
        if not events or events[0].details.get("reason") != "during_run":
            continue
        moment = events[0].created_at
        run = db.scalar(
            select(ExternalBackupRun)
            .where(ExternalBackupRun.disk_id == disk.id, ExternalBackupRun.started_at <= moment)
            .order_by(ExternalBackupRun.started_at.desc())
            .limit(1)
        )
        if run is None or run_reached_datastore(run):
            continue
        disk.verify_recommended = None
        disk.unsafe_removal_at = None
        db.add(disk)
        db.add(
            ActivityEvent(
                created_at=datetime.utcnow(),
                category="disk",
                action="verify_flag_cleared",
                status="info",
                summary=f"{disk.display_name} ({disk.serial_number}): verification no longer required — run #{run.id} failed before the disk was handed to PBS.",
                details={"disk_id": disk.id, "run_id": run.id},
            )
        )
        cleared += 1
    if cleared:
        db.commit()
    return cleared
