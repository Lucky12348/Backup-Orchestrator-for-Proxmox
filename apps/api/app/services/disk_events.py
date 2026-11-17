"""Disk events (disk.added / disk.removed / disk.updated) published after an
agent disk report, plus the history of the hot-plug events the agent saw.

Payloads carry what the UI needs to draw a disk card without another request,
and a stable `reject_reason` code the UI translates.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models.activity_event import ActivityEvent
from app.models.external_disk import ExternalDisk
from app.services import events
from app.services.pbs_guards import MIN_DISK_SIZE_GIB

ACTIVITY_CATEGORY = "disk"
# Sizes are binary everywhere (agent `bytes_to_gb` = GiB, shown as Gio/Tio):
# `capacity_gb` is compared with the minimum as is. Default of the
# MIN_DISK_SIZE_GIB setting (Settings › General, Phase 5g).
MIN_DISK_SIZE_GB = MIN_DISK_SIZE_GIB
# operator_decision values: "ignored" (until plugged again), "rejected"
# (always), "hidden" (not listed, Phase 5g), "approved_small" (approved
# although smaller than the minimum, after a confirmation of the risk).
HIDDEN = "hidden"
APPROVED_SMALL = "approved_small"


def min_disk_size_gib() -> int:
    from app.core.config import get_settings

    return int(getattr(get_settings(), "min_disk_size_gib", MIN_DISK_SIZE_GIB) or MIN_DISK_SIZE_GIB)


def reject_reason(disk: ExternalDisk) -> str | None:
    """Why a detected disk cannot be used, or None."""
    if (disk.capacity_gb or 0) <= 0 or disk.candidate_type == "unusable":
        return "zero_size"
    if disk.capacity_gb < min_disk_size_gib() and disk.operator_decision != APPROVED_SMALL:
        return "too_small"
    if disk.operator_decision == "rejected":
        return "operator"
    return None


def is_hidden(disk: ExternalDisk) -> bool:
    return disk.operator_decision == HIDDEN


def is_ejected(disk: ExternalDisk) -> bool:
    """Safely ejected and not unplugged since (B25)."""
    return (disk.handoff_status or "").strip().casefold() == "ejected"


def disk_status(disk: ExternalDisk) -> str:
    """new | known | ignored | ejected | unplugged | rejected | hidden (B25:
    "ejected" until the disk is unplugged, see services/disks.py)."""
    if is_hidden(disk):
        return "hidden"
    if reject_reason(disk):
        return "rejected"
    if is_ejected(disk):
        return "ejected"
    if not disk.connected:
        # Phase 5f.2: pulled out while handed to the PBS VM.
        return "pulled" if (disk.handoff_status or "") == "pulled" else "unplugged"
    if disk.trusted:
        return "known"
    return "ignored" if disk.operator_decision == "ignored" else "new"


def disk_event_payload(disk: ExternalDisk) -> dict[str, Any]:
    return {
        "id": disk.id,
        "serial_number": disk.serial_number,
        "display_name": disk.display_name,
        "model_name": disk.model_name,
        "capacity_gb": disk.capacity_gb,
        "filesystem_type": disk.filesystem_type,
        "connected": bool(disk.connected),
        "trusted": bool(disk.trusted),
        "status": disk_status(disk),
        "reject_reason": reject_reason(disk),
        "min_size_gb": min_disk_size_gib(),
        "hostname": disk.reported_by_hostname,
        "handoff_status": disk.handoff_status,
        "verify_recommended": bool(disk.verify_recommended),
    }


def snapshot(disk: ExternalDisk) -> tuple[Any, ...]:
    """Fields whose change is worth a disk.updated event."""
    return (
        disk.capacity_gb,
        disk.filesystem_type,
        disk.mount_path,
        bool(disk.connected),
        bool(disk.trusted),
        disk.filesystem_free_gb,
        disk.display_name,
        disk.handoff_status,
        disk.operator_decision,
    )


def publish_report_events(
    added: list[ExternalDisk], removed: list[ExternalDisk], updated: list[ExternalDisk]
) -> None:
    for disk in added:
        events.publish("disk.added", disk_event_payload(disk))
    for disk in removed:
        events.publish("disk.removed", disk_event_payload(disk))
    for disk in updated:
        events.publish("disk.updated", disk_event_payload(disk))


def record_hotplug_history(db: Session, hostname: str, hotplug: list[dict[str, str]]) -> None:
    """Journal the udev events the agent reported (added/removed + serial)."""
    for item in hotplug:
        action = item.get("action")
        serial = item.get("serial") or "?"
        if action not in {"added", "removed"}:
            continue
        db.add(
            ActivityEvent(
                created_at=datetime.utcnow(),
                category=ACTIVITY_CATEGORY,
                action=f"usb_{action}",
                status="info",
                summary=f"USB disk {serial} {'plugged in' if action == 'added' else 'unplugged'} on {hostname}",
                details={"serial": serial, "hostname": hostname, "model": item.get("model")},
            )
        )
