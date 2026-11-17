"""Proof that a filesystem holds a rotation disk's PBO datastore, kept by PBO
itself — independent of the PBS configuration, which may be deleted.

Recorded on the disk (`external_disks`) whenever PBO creates, reuses or
re-creates its datastore:
  pbs_backing_device       filesystem UUID (PBS `backing-device`)
  pbs_datastore_path       path of the datastore relative to that filesystem
  pbs_datastore_name       datastore name; serial_number = the disk
  pbs_marker_source        created | pbs_config | run_log
  pbs_marker_recorded_at

Back-fill for disks prepared before this record existed (`backfill`):
1. the PBS configuration still exists and carries `pbo:serial=<serial>` →
   its `backing-device` and `path` (source "pbs_config");
2. otherwise the journals of the disk's runs: PBS logs
   `temporarily mounting '<uuid>' to '<tmp dir>'` every time it mounts a
   removable datastore (do_mount_device), the UUID being the datastore's
   `backing-device` → the most recent one (source "run_log").
   The relative path is then the datastore name: PBS creates removable
   datastores (POST /nodes/{node}/disks/directory, removable-datastore=1) with
   `"path": name`. A wrong path cannot do harm: reuse-datastore only opens an
   existing chunk store (`.chunks`) and fails otherwise.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ExternalBackupRun, ExternalDisk

MOUNT_LINE_RE = re.compile(r"temporarily mounting '([^']+)'")
UUID_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z-]{3,63}$")


def uuid_from_logs(texts: list[str | None]) -> str | None:
    """The filesystem UUID of the last `temporarily mounting '<uuid>'` line
    (texts ordered oldest → newest)."""
    found: str | None = None
    for text in texts:
        for match in MOUNT_LINE_RE.finditer(text or ""):
            value = match.group(1).strip().rsplit("/", 1)[-1]  # also /dev/disk/by-uuid/<uuid>
            if UUID_RE.match(value):
                found = value
    return found


def default_path(datastore_name: str) -> str:
    """PBS gives a removable datastore created on a disk the path `<name>`."""
    return datastore_name


def record(disk: ExternalDisk, *, uuid: str | None, path: str | None, source: str, now: datetime | None = None) -> bool:
    changed = False
    if uuid and uuid != disk.pbs_backing_device:
        disk.pbs_backing_device = uuid
        changed = True
    if path and path != disk.pbs_datastore_path:
        disk.pbs_datastore_path = path
        changed = True
    if changed or not disk.pbs_marker_source:
        disk.pbs_marker_source = source
        disk.pbs_marker_recorded_at = now or datetime.utcnow()
        changed = True
    return changed


def backfill_disk(db: Session, disk: ExternalDisk, configs: list[dict[str, Any]] | None) -> bool:
    """Fills the proof of a prepared disk that has none (see module doc)."""
    from app.services.pbs_guards import has_pbo_marker

    name = disk.pbs_datastore_name
    if not (disk.prepared_as_pbs_datastore and name):
        return False
    if disk.pbs_backing_device and disk.pbs_datastore_path:
        return False
    config = next((cfg for cfg in configs or [] if cfg.get("name") == name), None)
    if config is not None and has_pbo_marker(config, disk.serial_number) and config.get("backing-device"):
        return record(disk, uuid=str(config["backing-device"]), path=str(config.get("path") or "") or default_path(name), source="pbs_config")
    uuid = disk.pbs_backing_device
    source = disk.pbs_marker_source or "run_log"
    if not uuid:
        runs = db.scalars(
            select(ExternalBackupRun).where(ExternalBackupRun.disk_id == disk.id).order_by(ExternalBackupRun.started_at.asc())
        ).all()
        uuid = uuid_from_logs([run.stdout_log for run in runs])
        source = "run_log"
    if not uuid:
        return False
    return record(disk, uuid=uuid, path=disk.pbs_datastore_path or default_path(name), source=source)


def backfill(db: Session, configs: list[dict[str, Any]] | None) -> list[int]:
    changed = [
        disk.id
        for disk in db.scalars(select(ExternalDisk).where(ExternalDisk.prepared_as_pbs_datastore.is_(True)))
        if backfill_disk(db, disk, configs)
    ]
    if changed:
        db.commit()
    return changed
