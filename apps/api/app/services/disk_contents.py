"""What is on a rotation disk (Phase 5f).

Read in PBS (`GET /admin/datastore/{store}/snapshots`) while the disk's
datastore is mounted — after each run, or on demand — and cached on the disk
(`contents_cache`, `contents_cached_at`) so the disk sheet still shows it
while the disk is unplugged ("as of <date>").

Cache shape:
  {"items": [{"group": "vm/100", "type": "vm", "id": "100", "name": "web",
              "versions": 4, "oldest": iso, "newest": iso,
              "size_bytes": <newest snapshot size>}],
   "snapshots": ["vm/100/1700000000", …]}     # keys, for copy estimates/diffs

The reverse view (Resources page): which disks hold a copy of a VM/CT, and
the date of the most recent one (`copies_by_group`).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ExternalDisk, VirtualMachine
from app.models.virtual_machine import VMType

MAX_SNAPSHOT_KEYS = 20000


def snapshot_key(snapshot: dict[str, Any]) -> str | None:
    kind, ident, when = snapshot.get("backup-type"), snapshot.get("backup-id"), snapshot.get("backup-time")
    if not kind or ident in (None, "") or not isinstance(when, int):
        return None
    return f"{kind}/{ident}/{when}"


def group_of_key(key: str) -> str:
    return key.rsplit("/", 1)[0]


def _iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds") + "Z"


def guest_names(db: Session) -> dict[str, str]:
    names: dict[str, str] = {}
    for vm in db.scalars(select(VirtualMachine)):
        if vm.external_id:
            names[f"{'ct' if vm.vm_type == VMType.CT else 'vm'}/{vm.external_id}"] = vm.name
    return names


def summarize(snapshots: list[dict[str, Any]], names: dict[str, str] | None = None) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = {}
    keys: list[str] = []
    for snapshot in snapshots:
        key = snapshot_key(snapshot)
        if key is None:
            continue
        keys.append(key)
        groups.setdefault(group_of_key(key), []).append(snapshot)
    items = []
    for group, entries in groups.items():
        entries.sort(key=lambda item: item["backup-time"])
        newest = entries[-1]
        kind, ident = group.split("/", 1)
        items.append(
            {
                "group": group,
                "type": kind,
                "id": ident,
                "name": (names or {}).get(group),
                "versions": len(entries),
                "oldest": _iso(entries[0]["backup-time"]),
                "newest": _iso(newest["backup-time"]),
                "size_bytes": newest.get("size") if isinstance(newest.get("size"), int) else None,
            }
        )
    items.sort(key=lambda item: (item["type"], _numeric(item["id"]), item["id"]))
    return {"items": items, "snapshots": sorted(keys)[:MAX_SNAPSHOT_KEYS]}


def _numeric(value: str) -> int:
    return int(value) if value.isdigit() else 1 << 62


def store_contents(db: Session, disk: ExternalDisk, snapshots: list[dict[str, Any]], *, now: datetime | None = None) -> None:
    disk.contents_cache = summarize(snapshots, guest_names(db))
    disk.contents_cached_at = now or datetime.utcnow()
    db.add(disk)


def cached_keys(disk: ExternalDisk) -> set[str] | None:
    cache = disk.contents_cache if isinstance(disk.contents_cache, dict) else None
    if cache is None:
        return None
    return {str(key) for key in cache.get("snapshots") or []}


def contents_payload(db: Session, disk: ExternalDisk) -> dict[str, Any]:
    cache = disk.contents_cache if isinstance(disk.contents_cache, dict) else {}
    names = guest_names(db)
    items = [{**item, "name": names.get(item.get("group")) or item.get("name")} for item in cache.get("items") or []]
    return {
        "disk_id": disk.id,
        "cached_at": disk.contents_cached_at,
        "items": items,
        "total_versions": sum(int(item.get("versions") or 0) for item in items),
    }


def _iso_dt(value: datetime | None) -> str | None:
    return value.isoformat(timespec="seconds") + "Z" if value else None


def copies_from_runs(db: Session, skip: set[int]) -> dict[str, list[dict[str, Any]]]:
    """Disks whose contents were never read (prepared before the cache, config
    deleted, unplugged): what their successful backups copied — the VM/CT of
    the group filter, or every VM/CT with a PBS backup for a whole-datastore
    copy — dated by the end of the most recent such run per group."""
    from app.models import ExternalBackupRun
    from app.models.backup_run import BackupRunStatus
    from app.services.run_log import run_kind

    disks = {disk.id: disk for disk in db.scalars(select(ExternalDisk))}
    all_groups = [group for group, vm in _guest_groups(db) if vm.last_backup_at is not None]
    newest: dict[tuple[str, int], datetime] = {}
    for run in db.scalars(
        select(ExternalBackupRun).where(
            ExternalBackupRun.status == BackupRunStatus.SUCCESS, ExternalBackupRun.finished_at.is_not(None)
        )
    ):
        disk = disks.get(run.disk_id)
        if disk is None or disk.id in skip or run.verify_only or run_kind(run, disk) != "backup":
            continue
        groups = [str(item).removeprefix("group:") for item in run.group_filter] if run.group_filter else all_groups
        for group in groups:
            key = (group, disk.id)
            if run.finished_at > newest.get(key, datetime.min):
                newest[key] = run.finished_at
    result: dict[str, list[dict[str, Any]]] = {}
    for (group, disk_id), at in newest.items():
        disk = disks[disk_id]
        result.setdefault(group, []).append(
            {
                "disk_id": disk.id,
                "disk_name": disk.display_name,
                "color": disk.color,
                "newest": _iso_dt(at),
                "versions": None,
                "cached_at": _iso_dt(at),
                "source": "runs",
            }
        )
    return result


def _guest_groups(db: Session) -> list[tuple[str, VirtualMachine]]:
    return [
        (f"{'ct' if vm.vm_type == VMType.CT else 'vm'}/{vm.external_id}", vm)
        for vm in db.scalars(select(VirtualMachine))
        if vm.external_id
    ]


def copies_by_group(db: Session) -> dict[str, list[dict[str, Any]]]:
    """{"vm/100": [{"disk_id", "disk_name", "color", "newest", "versions",
    "cached_at", "source"}]}, newest first. Per disk: its cached contents
    (read in PBS, kept while unplugged or after its PBS config was deleted),
    else what its successful runs copied."""
    result: dict[str, list[dict[str, Any]]] = {}
    cached: set[int] = set()
    for disk in db.scalars(select(ExternalDisk).where(ExternalDisk.contents_cache.is_not(None))):
        cache = disk.contents_cache if isinstance(disk.contents_cache, dict) else {}
        if not cache.get("items"):
            continue
        cached.add(disk.id)
        for item in cache.get("items") or []:
            group = item.get("group")
            if not group:
                continue
            result.setdefault(group, []).append(
                {
                    "disk_id": disk.id,
                    "disk_name": disk.display_name,
                    "color": disk.color,
                    "newest": item.get("newest"),
                    "versions": item.get("versions"),
                    "cached_at": _iso_dt(disk.contents_cached_at),
                    "source": "contents",
                }
            )
    for group, copies in copies_from_runs(db, cached).items():
        result.setdefault(group, []).extend(copies)
    for copies in result.values():
        copies.sort(key=lambda copy: copy.get("newest") or "", reverse=True)
    return result
