"""Disk profile (Phase 5f): kind deduced from the hardware, colour, retention,
usage history and usage alerts.

Pure helpers except `record_usage` (mutates the disk, may notify).
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from app.models import ExternalDisk

COLORS = ("slate", "blue", "green", "amber", "red", "violet")
DISK_KINDS = ("hdd_portable", "hdd_desktop", "ssd", "usb_stick", "nvme_enclosure", "unknown")
USAGE_HISTORY_MAX = 365
USAGE_WARN_PERCENT = 80
USAGE_CRITICAL_PERCENT = 90
USB2_MAX_MBPS = 480
DEFAULT_RETENTION: dict[str, int | None] = {"keep_last": 4, "keep_weekly": None, "keep_monthly": None}
RETENTION_KEYS = ("keep_last", "keep_weekly", "keep_monthly")
RETENTION_MAX = 1000

_NVME_RE = re.compile(r"nvme|rtl9210|jms58[13]|asm236[24]|asm2464", re.I)
_STICK_RE = re.compile(r"flash|cruzer|datatraveler|ultra\s*fit|ultra\s*luxe|usb\s*disk|stick|jumpdrive|thumb", re.I)
_DESKTOP_RE = re.compile(r"my\s*book|desktop|\bdesk\b|elements\s*desk|expansion\s*desk|d10|duo", re.I)
_PORTABLE_RE = re.compile(r"passport|portable|slim|canvio|elements|expansion|rugged|spzx", re.I)
# 2.5" drives top out around 5 TB: bigger spinning disks are 3.5" desktop drives.
PORTABLE_MAX_GIB = 4700


def disk_kind(
    *,
    rotational: bool | None,
    transport: str | None,
    capacity_gb: int | None,
    model: str | None,
    usb_product: str | None = None,
) -> str:
    """hdd_portable (2.5"), hdd_desktop (3.5"), ssd, usb_stick, nvme_enclosure
    or unknown, from rotation, transport, size and model (heuristic)."""
    text = " ".join(filter(None, [model, usb_product]))
    if (transport or "").lower() == "nvme" or _NVME_RE.search(text):
        return "nvme_enclosure"
    if _STICK_RE.search(text):
        return "usb_stick"
    if rotational is False:
        return "ssd"
    if rotational is None and not text:
        return "unknown"
    if _DESKTOP_RE.search(text) or (capacity_gb or 0) > PORTABLE_MAX_GIB:
        return "hdd_desktop"
    if rotational or _PORTABLE_RE.search(text):
        return "hdd_portable"
    return "unknown"


def kind_of(disk: ExternalDisk) -> str:
    location = disk.usb_location if isinstance(disk.usb_location, dict) else {}
    return disk_kind(
        rotational=disk.rotational,
        transport=disk.transport,
        capacity_gb=disk.capacity_gb,
        model=disk.model_name or disk.reported_model_name,
        usb_product=location.get("product"),
    )


# --- retention -------------------------------------------------------------------------------
class RetentionError(ValueError):
    pass


def normalize_retention(value: dict[str, Any] | None) -> dict[str, int | None]:
    if value is None:
        return dict(DEFAULT_RETENTION)
    result: dict[str, int | None] = {}
    for key in RETENTION_KEYS:
        raw = value.get(key)
        if raw in (None, "", 0):
            result[key] = None
            continue
        try:
            number = int(raw)
        except (TypeError, ValueError) as exc:
            raise RetentionError(f"{key}: expected an integer") from exc
        if not 1 <= number <= RETENTION_MAX:
            raise RetentionError(f"{key}: must be between 1 and {RETENTION_MAX}")
        result[key] = number
    if not any(result.values()):
        # A prune without any keep-* option would delete every snapshot.
        raise RetentionError("at least one keep option is required")
    return result


def retention_of(disk: ExternalDisk) -> dict[str, int | None]:
    try:
        return normalize_retention(disk.retention if isinstance(disk.retention, dict) else None)
    except RetentionError:
        return dict(DEFAULT_RETENTION)


def prune_options(retention: dict[str, int | None]) -> dict[str, int]:
    """PBS prune-datastore parameters ("keep-last"…)."""
    return {key.replace("_", "-"): int(value) for key, value in retention.items() if value}


# --- usage --------------------------------------------------------------------------------------
def usage_percent(used_gb: int | None, total_gb: int | None) -> float | None:
    if used_gb is None or not total_gb:
        return None
    return round(100.0 * used_gb / total_gb, 1)


def usage_level(percent: float | None) -> int | None:
    if percent is None:
        return None
    if percent >= USAGE_CRITICAL_PERCENT:
        return USAGE_CRITICAL_PERCENT
    if percent >= USAGE_WARN_PERCENT:
        return USAGE_WARN_PERCENT
    return None


def record_usage(disk: ExternalDisk, *, now: datetime | None = None, notify: bool = True) -> int | None:
    """Adds today's usage sample (one per day, the latest wins) and raises the
    80 % / 90 % alert once per level. Returns the newly crossed level."""
    used, total = disk.filesystem_used_gb, disk.filesystem_total_gb
    if used is None or not total:
        return None
    moment = now or datetime.utcnow()
    sample = {"at": moment.isoformat(timespec="seconds") + "Z", "used_gb": int(used), "total_gb": int(total)}
    history = [item for item in (disk.usage_history or []) if isinstance(item, dict)]
    if history and str(history[-1].get("at", ""))[:10] == sample["at"][:10]:
        history[-1] = sample
    else:
        history.append(sample)
    disk.usage_history = history[-USAGE_HISTORY_MAX:]

    level = usage_level(usage_percent(used, total))
    previous = disk.usage_alert_level
    disk.usage_alert_level = level
    if level is not None and (previous is None or level > previous):
        if notify:
            from app.services.notifications import notify_disk_space

            notify_disk_space(disk.display_name or disk.serial_number, level, int(used), int(total))
        return level
    return None


def is_usb2(disk: ExternalDisk) -> bool:
    return disk.usb_speed_mbps is not None and 0 < disk.usb_speed_mbps <= USB2_MAX_MBPS
