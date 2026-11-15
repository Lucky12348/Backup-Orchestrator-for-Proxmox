"""Server-side safety guards for PBS API mode (re-implements the PBS agent's checks).

In agent mode these checks run on the PBS VM inside the root agent
(`_assert_safe_dedicated_disk`, `_assert_disk_hosts_no_pbs_datastore`, the
`.pbo-datastore.json` marker, the 32 GB minimum…). In API mode nothing runs
on PBS but PBS itself, so the same decisions are taken here, from data read
through the PBS API, *before* any destructive call (wipedisk / directory).

PBO marker: the API cannot read a file on the disk, so ownership is recorded
in the datastore configuration comment: `pbo:serial=<serial>`. A filesystem
bound to a datastore without that marker is never reused nor formatted.

Every function here is pure (no I/O) so it can be tested exhaustively.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

MIN_DISK_SIZE_GIB = 32
PBO_MARKER_PREFIX = "pbo:serial="
MAX_DATASTORE_NAME_LENGTH = 32  # PBS datastore name: 3-32 characters
# `used` values of /nodes/{node}/disks/list that mean "in use by the system".
SYSTEM_DISK_USAGES = frozenset({"mounted", "lvm", "zfs", "devicemapper"})
# Partition `used` values that must never be wiped.
SYSTEM_PARTITION_USAGES = frozenset({"lvm", "zfs", "zfsreserved", "efi", "bios"})
# Filesystems that may hold someone's data/datastore: never formatted without
# a PBO marker (mirrors the agent's "ext4 partition without marker" refusal).
DATA_FILESYSTEMS = frozenset({"ext4", "xfs", "btrfs"})


class GuardViolation(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class PreparationPlan:
    action: str  # "reuse" | "format"
    datastore_name: str
    device: str  # PBS device name, e.g. "sdb"
    devpath: str | None = None
    backing_device: str | None = None
    reasons: list[str] = field(default_factory=list)


def pbo_marker(serial: str) -> str:
    return f"{PBO_MARKER_PREFIX}{serial}"


def has_pbo_marker(datastore_config: dict[str, Any], serial: str) -> bool:
    comment = str(datastore_config.get("comment") or "")
    return pbo_marker(serial) in comment.split()


def fit_datastore_name(name: str) -> str:
    """PBS limits datastore names to 32 characters; keep it deterministic."""
    if len(name) <= MAX_DATASTORE_NAME_LENGTH:
        return name
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:8]
    return f"{name[: MAX_DATASTORE_NAME_LENGTH - 9].rstrip('-')}-{digest}"


def size_gib(size_bytes: Any) -> int:
    try:
        value = int(size_bytes)
    except (TypeError, ValueError):
        return 0
    return round(value / (1024**3)) if value > 0 else 0


def partition_uuids(pbs_disk: dict[str, Any]) -> set[str]:
    return {
        str(part["uuid"]).strip()
        for part in pbs_disk.get("partitions") or []
        if isinstance(part, dict) and part.get("uuid")
    }


def assert_disk_approved(disk_record: Any) -> None:
    """Only disks the operator explicitly trusted in the UI may be touched."""
    if disk_record is None:
        raise GuardViolation("disk_unknown", "Disk is not registered in the orchestrator; refusing any PBS change.")
    if not getattr(disk_record, "trusted", False):
        raise GuardViolation("disk_not_approved", f"Disk `{disk_record.serial_number}` is not approved (trusted) for backups.")


def assert_store_idle(store: str, running_tasks: list[dict[str, Any]]) -> None:
    from app.services.pbs_client import task_worker_tokens

    for task in running_tasks:
        if store in task_worker_tokens(task):
            raise GuardViolation(
                "datastore_active",
                f"A PBS task is currently running on datastore `{store}` ({task.get('worker_type') or 'task'}).",
            )


def plan_dedicated_preparation(
    *,
    serial: str,
    disk_record: Any,
    pbs_disk: dict[str, Any],
    datastore_configs: list[dict[str, Any]],
    target_name: str,
    confirmation: bool,
    running_tasks: list[dict[str, Any]],
) -> PreparationPlan:
    """Decide between reusing a PBO datastore and formatting the disk — or refuse."""
    assert_disk_approved(disk_record)
    device = str(pbs_disk.get("name") or "").strip()
    if not device:
        raise GuardViolation("disk_not_found", f"Disk `{serial}` is not visible on PBS.")

    uuids = partition_uuids(pbs_disk)
    bound = [cfg for cfg in datastore_configs if str(cfg.get("backing-device") or "") in uuids]
    if len(bound) > 1:
        names = ", ".join(str(cfg.get("name")) for cfg in bound)
        raise GuardViolation("datastore_ambiguous", f"Disk `{serial}` backs several PBS datastores ({names}); refusing.")
    if bound:
        config = bound[0]
        name = str(config.get("name"))
        if not has_pbo_marker(config, serial):
            raise GuardViolation(
                "datastore_exists",
                f"Disk `{serial}` already backs PBS datastore `{name}` without the PBO marker; refusing to reuse or format it.",
            )
        assert_store_idle(name, running_tasks)
        return PreparationPlan(
            action="reuse",
            datastore_name=name,
            device=device,
            devpath=pbs_disk.get("devpath"),
            backing_device=str(config.get("backing-device")),
            reasons=["PBO-marked removable datastore found on this disk"],
        )

    taken = next((cfg for cfg in datastore_configs if cfg.get("name") == target_name), None)
    if taken is not None:
        raise GuardViolation(
            "datastore_name_taken",
            f"A PBS datastore named `{target_name}` already exists on another device; refusing to create it.",
        )

    # --- destructive path: every check must pass -----------------------------
    reasons: list[str] = []
    gib = size_gib(pbs_disk.get("size"))
    if gib < MIN_DISK_SIZE_GIB:
        raise GuardViolation("disk_too_small", f"Refusing to prepare `{device}`: {gib} GiB < {MIN_DISK_SIZE_GIB} GiB minimum.")
    usage = str(pbs_disk.get("used") or "").strip().lower()
    if usage in SYSTEM_DISK_USAGES:
        raise GuardViolation("disk_in_use", f"Refusing to format `{device}`: PBS reports it in use ({usage}).")
    for part in pbs_disk.get("partitions") or []:
        if not isinstance(part, dict):
            continue
        part_name = part.get("name") or part.get("devpath") or "?"
        if part.get("mounted"):
            raise GuardViolation("partition_mounted", f"Refusing to format `{device}`: partition `{part_name}` is mounted.")
        part_usage = str(part.get("used") or "").strip().lower()
        if part_usage in SYSTEM_PARTITION_USAGES:
            raise GuardViolation("partition_system", f"Refusing to format `{device}`: partition `{part_name}` is {part_usage}.")
        filesystem = str(part.get("filesystem") or "").strip().lower()
        if filesystem in DATA_FILESYSTEMS:
            raise GuardViolation(
                "partition_has_data",
                f"Refusing to format `{device}`: partition `{part_name}` holds a {filesystem} filesystem without PBO marker. "
                "Wipe it manually after checking its content.",
            )
        if filesystem:
            reasons.append(f"partition {part_name} ({filesystem}) will be erased")
    if not confirmation:
        raise GuardViolation("confirmation_required", "Dedicated PBS datastore preparation requires destructive confirmation.")
    return PreparationPlan(action="format", datastore_name=target_name, device=device, devpath=pbs_disk.get("devpath"), reasons=reasons)


def match_pbs_disk(pbs_disks: list[dict[str, Any]], identifiers: list[str]) -> dict[str, Any] | None:
    """Find exactly one PBS disk for any identifier (serial, wwn, name, devpath)."""
    wanted = [value.strip() for value in identifiers if value and value.strip()]
    for identifier in wanted:
        lowered = identifier.lower()
        matches = [
            disk
            for disk in pbs_disks
            if lowered
            in {
                str(disk.get(key) or "").strip().lower()
                for key in ("serial", "wwn", "name", "devpath")
                if disk.get(key)
            }
        ]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise GuardViolation("disk_ambiguous", f"Identifier `{identifier}` matches several PBS disks; refusing.")
    return None
