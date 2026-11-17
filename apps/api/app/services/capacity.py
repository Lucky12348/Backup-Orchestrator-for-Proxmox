"""Space needed by the next copy vs free space on the disk (Phase 5f).

Before a backup starts, PBO compares the snapshots of the source datastore
(filtered by the VM/CT selection) with the snapshots already on the disk
(the disk's cached contents, read in PBS at the end of the previous run).

PBS stores deduplicated chunks, so a snapshot's `size` (sum of its archives)
is a logical size. The estimate is deliberately simple and on the safe side:
  * a VM/CT not yet on the disk costs its newest snapshot's size (first full copy);
  * each other missing snapshot costs INCREMENTAL_RATIO of its size (changed chunks).
It is compared with the free space PBS reported for the disk's datastore
(or the whole disk when it has never been prepared). Unknown → no refusal.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from sqlalchemy.orm import Session

from app.models import ExternalDisk
from app.services.disk_contents import cached_keys, group_of_key, snapshot_key

GIB = 1024**3
INCREMENTAL_RATIO = 0.10
# ext4 + PBS chunk store overhead on a freshly formatted disk.
FORMAT_OVERHEAD = 0.98
SOURCE_CACHE_SECONDS = 60.0

_cache_lock = threading.Lock()
_source_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}


def reset_cache() -> None:
    with _cache_lock:
        _source_cache.clear()


def _selected(snapshot: dict[str, Any], group_filter: list[str] | None) -> bool:
    if not group_filter:
        return True
    key = snapshot_key(snapshot)
    return key is not None and f"group:{group_of_key(key)}" in group_filter


def estimate_bytes(
    source_snapshots: list[dict[str, Any]],
    disk_keys: set[str],
    group_filter: list[str] | None = None,
) -> dict[str, int]:
    on_disk_groups = {group_of_key(key) for key in disk_keys}
    # PBS's sync skips versions older than the newest one already on the
    # target (B48: versions removed by the disk's retention are not "new").
    newest_on_disk: dict[str, int] = {}
    for key in disk_keys:
        group, _, when = key.rpartition("/")
        if when.isdigit():
            newest_on_disk[group] = max(newest_on_disk.get(group, 0), int(when))
    missing: dict[str, list[dict[str, Any]]] = {}
    for snapshot in source_snapshots:
        key = snapshot_key(snapshot)
        if key is None or key in disk_keys or not _selected(snapshot, group_filter):
            continue
        if int(snapshot["backup-time"]) <= newest_on_disk.get(group_of_key(key), -1):
            continue
        missing.setdefault(group_of_key(key), []).append(snapshot)
    needed = 0.0
    for group, snapshots in missing.items():
        sizes = [int(item.get("size") or 0) for item in sorted(snapshots, key=lambda item: item["backup-time"])]
        if group in on_disk_groups:
            needed += INCREMENTAL_RATIO * sum(sizes)
        else:
            needed += sizes[-1] + INCREMENTAL_RATIO * sum(sizes[:-1])
    return {
        "needed_bytes": int(needed),
        "new_snapshots": sum(len(items) for items in missing.values()),
        "new_groups": len([group for group in missing if group not in on_disk_groups]),
    }


def free_bytes(disk: ExternalDisk) -> int | None:
    if disk.prepared_as_pbs_datastore and disk.pbs_datastore_name:
        return disk.filesystem_free_gb * GIB if disk.filesystem_free_gb is not None else None
    if disk.capacity_gb:
        return int(disk.capacity_gb * GIB * FORMAT_OVERHEAD)
    return None


def _source_snapshots(client: Any, store: str) -> list[dict[str, Any]]:
    now = time.monotonic()
    with _cache_lock:
        cached = _source_cache.get(store)
        if cached and now - cached[0] < SOURCE_CACHE_SECONDS:
            return cached[1]
    snapshots = client.list_snapshots(store)
    with _cache_lock:
        _source_cache[store] = (now, snapshots)
    return snapshots


def check_space(
    db: Session,
    disk: ExternalDisk,
    *,
    group_filter: list[str] | None = None,
    datastore: str | None = None,
    client: Any | None = None,
) -> dict[str, Any] | None:
    """{"needed_bytes", "free_bytes", "new_snapshots", "new_groups",
    "sufficient": bool | None}. None when PBS cannot be asked (agent mode,
    not configured, unreachable): nothing is refused on a guess."""
    from app.services.settings_service import effective_settings

    settings = effective_settings(db)
    store = datastore or settings.pbs_datastore
    if settings.pbs_mode != "api" or not store or not settings.pbs_api_url:
        return None
    if client is None:
        from app.services.pbs_client import PBSClient

        client = PBSClient(settings)
    try:
        source = _source_snapshots(client, store)
    except Exception:  # noqa: BLE001 — unreachable PBS: no estimate
        return None
    prepared = bool(disk.prepared_as_pbs_datastore and disk.pbs_datastore_name)
    keys = cached_keys(disk) if prepared else set()
    estimate = estimate_bytes(source, keys or set(), group_filter)
    free = free_bytes(disk)
    # A prepared disk whose contents were never read: no estimate (B48: an
    # upper bound counting every source version misled), never a refusal.
    contents_known = not prepared or keys is not None
    known = free is not None and contents_known
    return {
        **estimate,
        "free_bytes": free,
        "known": known,
        "contents_known": contents_known,
        "sufficient": (estimate["needed_bytes"] <= free) if known else None,
    }


def space_reason(estimate: dict[str, Any] | None) -> dict[str, Any] | None:
    if not estimate or estimate.get("sufficient") is not False:
        return None
    needed, free = estimate["needed_bytes"], estimate["free_bytes"]
    return {
        "code": "insufficient_space",
        "message": (
            f"Not enough space on the disk: about {needed / GIB:.1f} GiB to copy, {free / GIB:.1f} GiB free."
        ),
        "needed_gib": round(needed / GIB, 1),
        "free_gib": round(free / GIB, 1),
    }
