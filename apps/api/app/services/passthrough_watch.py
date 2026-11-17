"""Disks handed to the PBS VM: are they still plugged in? (Phase 5f.2)

A disk passed through to the PBS VM (`qm set <vmid> -usbN host=<port>`) has no
block device on the Proxmox host any more, so the block-level udev events do
not see it being pulled out. What does:

* the USB device itself stays in the host's sysfs while it is passed through
  (QEMU only claims it) and disappears when it is unplugged — the host agent
  sends the list with every report (udev USB events trigger one) and serves it
  at `GET /usb/devices` (cheap sysfs read);
* `reconcile()` runs ~every 30 s, and at app start, only while PBO believes a
  disk is handed to PBS (or holds an open USB mapping): host sysfs first; when
  the host agent cannot tell (old agent, unreachable), PBS's disk list
  (`/inspect-disk`) two checks in a row.

A disk gone while handed to PBS (`handle_pulled`), in this order:
1. the `usbN` mapping is removed from the PBS VM first: left there, it would
   hand any device later plugged into that port to PBS;
2. a run on that disk: its PBS task is stopped, the run fails with a clear
   journal line;
3. PBS: the removable datastore is unmounted (no error if the device is gone,
   stacked stale mounts are unmounted one by one);
4. the disk is "pulled" (unplugged without eject): verify recommended,
   activity line, notification, `disk.removed` event (toast).

USB mappings PBO puts on the PBS VM are tracked (`pbs_usb_mappings`); at
start, the ones of disks no longer in use are removed — only when the VM
config line still equals what PBO wrote.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import ExternalDisk, PbsUsbMapping

logger = logging.getLogger(__name__)

PORT_RE = re.compile(r"^\d+-\d+(?:\.\d+)*$")
# Run steps after a successful sync (B44: "copy finished, cleanup interrupted").
AFTER_SYNC_STEPS = frozenset({"cleanup", "verify", "verify_failed", "success", "auto_eject", "eject", "eject_flush", "ejected", "release"})
PBS_MISSES_BEFORE_GONE = 2
WATCH_INTERVAL_SECONDS = 30.0
_pbs_misses: dict[int, int] = {}


# --- mappings ---------------------------------------------------------------------------------------
def open_mappings(db: Session) -> list[PbsUsbMapping]:
    return list(db.scalars(select(PbsUsbMapping).where(PbsUsbMapping.released_at.is_(None))))


def release_mappings(db: Session, *, vmid: int, slot: str, reason: str, now: datetime | None = None) -> None:
    for row in db.scalars(
        select(PbsUsbMapping).where(PbsUsbMapping.vmid == vmid, PbsUsbMapping.slot == slot, PbsUsbMapping.released_at.is_(None))
    ):
        row.released_at = now or datetime.utcnow()
        row.release_reason = reason


def record_mapping(db: Session, disk: ExternalDisk, *, vmid: int, slot: str, host_value: str, now: datetime | None = None) -> None:
    """A usbN line PBO just wrote on the PBS VM."""
    moment = now or datetime.utcnow()
    release_mappings(db, vmid=vmid, slot=slot, reason="replaced", now=moment)
    db.add(PbsUsbMapping(vmid=vmid, slot=slot, host_value=host_value, disk_id=disk.id, disk_serial=disk.serial_number, created_at=moment))


def _backfill(db: Session, disks: list[ExternalDisk], vmid: int) -> None:
    """Mappings written before they were tracked (disk fields only)."""
    tracked = {(row.vmid, row.slot) for row in open_mappings(db)}
    for disk in disks:
        if disk.pbs_handoff_slot and disk.proxmox_usb_mapping and (vmid, disk.pbs_handoff_slot) not in tracked:
            record_mapping(db, disk, vmid=vmid, slot=disk.pbs_handoff_slot, host_value=disk.proxmox_usb_mapping)


# --- presence ------------------------------------------------------------------------------------------
def handed_off(db: Session) -> list[ExternalDisk]:
    return list(
        db.scalars(
            select(ExternalDisk).where(
                ExternalDisk.pbs_handoff_slot.is_not(None), ExternalDisk.serial_number.not_like("agent-report::%")
            )
        )
    )


def disk_port(disk: ExternalDisk) -> str | None:
    location = disk.usb_location if isinstance(disk.usb_location, dict) else {}
    port = str(location.get("port_id") or "")
    if PORT_RE.match(port):
        return port
    mapping = str(disk.proxmox_usb_mapping or "")
    return mapping if PORT_RE.match(mapping) else None


def is_present(disk: ExternalDisk, devices: list[dict[str, Any]] | None) -> bool | None:
    """True / False from the host's USB devices; None when it cannot tell."""
    if devices is None:
        return None
    port = disk_port(disk)
    if port is None:
        return None
    device = next((item for item in devices if str(item.get("port_id")) == port), None)
    if device is None:
        return False
    location = disk.usb_location if isinstance(disk.usb_location, dict) else {}
    known, seen = location.get("serial"), device.get("serial")
    if known and seen and str(known) != str(seen):
        return False  # another device now sits on that port
    return True


def _host_devices(host_client: Any) -> list[dict[str, Any]] | None:
    try:
        result = host_client.get("/usb/devices")
    except Exception as exc:  # noqa: BLE001 — old agent (no route) or unreachable: no verdict
        logger.debug("USB device list unavailable: %s", exc)
        return None
    devices = (result.payload or {}).get("devices") if hasattr(result, "payload") else None
    return devices if isinstance(devices, list) else None


def _visible_on_pbs(pbs_client: Any, disk: ExternalDisk) -> bool | None:
    try:
        pbs_client.post("/inspect-disk", {"disk": disk.serial_number})
    except Exception as exc:  # noqa: BLE001
        payload = getattr(exc, "payload", None) or {}
        return False if payload.get("error_code") == "disk_not_found" else None
    return True


# --- a disk pulled out while handed to PBS --------------------------------------------------------------
def handle_pulled(
    db: Session,
    disk: ExternalDisk,
    *,
    reason: str,
    host_client: Any | None = None,
    pbs_client: Any | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    from app.services import disk_guards
    from app.services.disk_events import disk_event_payload
    from app.services.events import publish
    from app.services.external_backups import append_external_backup_run_log
    from app.services.host_agent import get_host_agent_client, get_pbs_agent_client

    settings = get_settings()
    moment = now or datetime.utcnow()
    host = host_client or get_host_agent_client()
    done: dict[str, Any] = {"disk_id": disk.id, "detached": False, "datastore_offline": None, "run_id": None}
    run = disk_guards.active_run_for(db, disk.id)
    vmid = settings.pbs_execution_vm_id

    # 1. The mapping first: left on the VM, it gives PBS whatever is plugged next.
    slot = disk.pbs_handoff_slot
    if slot:
        try:
            host.post("/qemu/usb/detach", {"vmid": vmid, "slot": slot})
            done["detached"] = True
        except Exception as exc:  # noqa: BLE001 — retried by the orphan cleanup at start
            logger.warning("Could not remove %s from VM %s after %s was pulled out: %s", slot, vmid, disk.serial_number, exc)
        release_mappings(db, vmid=vmid, slot=slot, reason="pulled", now=moment)

    api = settings.pbs_mode == "api"
    pbs = pbs_client or (get_pbs_agent_client() if api else None)
    # 2. A run on that disk: stop its PBS task, fail it with a clear line.
    if run is not None:
        from app.services.external_backups import INTERRUPTION_MESSAGES, _apply_interruption

        done["run_id"] = run.id
        stage = "after_sync" if (run.current_step or "") in AFTER_SYNC_STEPS else "during_sync"
        # The journal line first (the flag closes the journal to normal lines).
        append_external_backup_run_log(db, run.id, step="disk_pulled", message=INTERRUPTION_MESSAGES[stage], stream="stderr")
        db.refresh(run)
        run.interrupted = stage
        _apply_interruption(run)
        db.add(run)
        # B46: data may still have been in the write cache: verify before trusting it.
        disk.verify_required = True
        db.commit()
        client = getattr(pbs, "client", None)
        if api and run.pbs_task_upid and client is not None:
            try:
                client.stop_task(run.pbs_task_upid)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Could not stop PBS task %s: %s", run.pbs_task_upid, exc)
    # 3. PBS: unmount the removable datastore (the device is gone: no error).
    if api and disk.pbs_datastore_name and pbs is not None:
        try:
            result = pbs.post("/offline-removable-datastore", {"datastore_name": disk.pbs_datastore_name})
            done["datastore_offline"] = (result.payload or {}).get("state")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not take %s offline on PBS: %s", disk.pbs_datastore_name, exc)
            done["datastore_offline"] = "error"
    # 4. The disk: unplugged without eject.
    disk_guards.mark_unsafe_removal(db, disk, reason=reason, now=moment)
    disk.verify_recommended = True
    disk.unsafe_removal_at = disk.unsafe_removal_at or moment
    disk.handoff_status = "pulled"
    disk.pbs_handoff_slot = None
    disk.proxmox_usb_mapping = None
    disk.pbs_visible = False
    disk.pbs_device_path = None
    disk.connected = False
    disk.presence_state = "absent"
    db.add(disk)
    db.commit()
    db.refresh(disk)
    _pbs_misses.pop(disk.id, None)
    publish("disk.removed", disk_event_payload(disk))
    return done


# --- orphan mappings -------------------------------------------------------------------------------------
def cleanup_orphan_mappings(db: Session, *, host_client: Any, devices: list[dict[str, Any]] | None) -> list[str]:
    """Removes the usbN lines PBO wrote for disks no longer in use."""
    from app.services import disk_guards
    from app.services.disk_handoff import _parse_qm_usb_config, _vm_config_value_matches_mapping

    removed: list[str] = []
    rows = open_mappings(db)
    configs: dict[int, dict[str, str] | None] = {}
    for row in rows:
        if row.vmid not in configs:
            try:
                result = host_client.post("/qemu/config", {"vmid": row.vmid})
                raw = (result.payload or {}).get("config") or result.stdout_log or ""
                configs[row.vmid] = _parse_qm_usb_config(str(raw))
            except Exception as exc:  # noqa: BLE001 — cannot read the VM: touch nothing
                logger.warning("Cannot read VM %s config for the USB mapping cleanup: %s", row.vmid, exc)
                configs[row.vmid] = None
        config = configs[row.vmid]
        if config is None:
            continue
        value = config.get(row.slot)
        if not value or not _vm_config_value_matches_mapping(value, row.host_value):
            row.released_at, row.release_reason = datetime.utcnow(), "gone"  # removed by someone, or another device's
            continue
        disk = db.get(ExternalDisk, row.disk_id) if row.disk_id else None
        in_use = disk is not None and (
            disk_guards.active_run_for(db, disk.id) is not None
            or (disk.pbs_handoff_slot == row.slot and is_present(disk, devices) is not False)
        )
        if in_use:
            continue
        try:
            host_client.post("/qemu/usb/detach", {"vmid": row.vmid, "slot": row.slot})
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not remove orphan mapping %s=%s: %s", row.slot, row.host_value, exc)
            continue
        row.released_at, row.release_reason = datetime.utcnow(), "orphan"
        removed.append(f"{row.slot}={row.host_value}")
        if disk is not None and disk.pbs_handoff_slot == row.slot:
            disk.pbs_handoff_slot = None
            disk.proxmox_usb_mapping = None
            disk.pbs_visible = False
    db.commit()
    if removed:
        logger.warning("Removed orphan USB mapping(s) from the PBS VM: %s", ", ".join(removed))
    return removed


# --- the watch ----------------------------------------------------------------------------------------------
def reconcile(db: Session, *, host_client: Any | None = None, pbs_client: Any | None = None, cleanup_orphans: bool = False) -> dict[str, Any]:
    from app.services import disk_guards
    from app.services.host_agent import get_host_agent_client, get_pbs_agent_client

    settings = get_settings()
    disks = handed_off(db)
    if not disks and not (cleanup_orphans and open_mappings(db)):
        return {"pulled": [], "orphans": [], "stale_mounts": clean_stale_mounts(db, settings)}
    _backfill(db, disks, settings.pbs_execution_vm_id)
    db.commit()
    host = host_client or get_host_agent_client()
    devices = _host_devices(host)
    pulled: list[int] = []
    for disk in disks:
        present = is_present(disk, devices)
        if present is None and settings.pbs_mode == "api" and disk_guards.active_run_for(db, disk.id) is None:
            # The host cannot tell: PBS's disk list, twice in a row (a hand-off
            # in progress is covered by its run).
            pbs = pbs_client or get_pbs_agent_client()
            visible = _visible_on_pbs(pbs, disk)
            if visible is False:
                _pbs_misses[disk.id] = _pbs_misses.get(disk.id, 0) + 1
                present = False if _pbs_misses[disk.id] >= PBS_MISSES_BEFORE_GONE else None
            elif visible:
                _pbs_misses.pop(disk.id, None)
        if present is False:
            handle_pulled(db, disk, reason="mounted_on_pbs", host_client=host, pbs_client=pbs_client)
            pulled.append(disk.id)
    orphans = cleanup_orphan_mappings(db, host_client=host, devices=devices) if cleanup_orphans else []
    stale = clean_stale_mounts(db, settings)
    return {"pulled": pulled, "orphans": orphans, "stale_mounts": stale}


def clean_stale_mounts(db: Session, settings: Any) -> list[str]:
    """B41: dead mounts left on the datastores of disks PBS is not using now
    (through the PBS VM's QEMU guest agent, see services/stale_mounts.py)."""
    from app.services import disk_guards, stale_mounts

    if not settings.host_agent_base_url or not settings.pbs_execution_vm_id:
        return []
    status = stale_mounts.guest_agent_status(settings)
    if not status or not status.get("active"):
        return []
    removed: list[str] = []
    for disk in db.scalars(select(ExternalDisk).where(ExternalDisk.pbs_datastore_name.is_not(None))):
        if disk.pbs_handoff_slot or disk_guards.active_run_for(db, disk.id) is not None:
            continue
        result = stale_mounts.clean(disk.pbs_datastore_name, settings=settings)
        removed.extend(str(item.get("source")) for item in (result or {}).get("removed") or [])
    return removed


async def watch(session_factory, interval_seconds: float = WATCH_INTERVAL_SECONDS) -> None:
    """At app start (with the orphan cleanup), then every ~30 s."""
    first = True
    while True:
        try:
            await asyncio.to_thread(_run_once, session_factory, first)
        except Exception:  # noqa: BLE001
            logger.warning("Passthrough watch failed", exc_info=True)
        first = False
        await asyncio.sleep(interval_seconds)


def _run_once(session_factory, cleanup_orphans: bool) -> None:
    with session_factory() as db:
        reconcile(db, cleanup_orphans=cleanup_orphans)
