"""SMART health of the USB disks (host role, Phase 5f).

Read with `smartctl` (package smartmontools, installed by install.sh) when a
disk is plugged in (hot-plug "added"), when it was never read, or once a day.
The result is cached per serial in the agent state directory so the 5-minute
timer report neither wakes the disk up nor re-runs smartctl.

Many USB enclosures do not relay SMART: smartctl is tried as is, then with
`-d sat` (SCSI/ATA translation); without data the status is "unknown".
`-n standby` never spins a sleeping disk up (the cached value is kept).

Status:
  danger   SMART overall-health failed, or NVMe critical warning
  warning  reallocated / pending / uncorrectable sectors, media errors,
           errors in the error or self-test log, attributes past threshold
  ok       health passed, none of the above
  unknown  no SMART data (bridge, smartctl missing, disk asleep never read)
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("agent.smart")

CACHE_FILE_NAME = "smart.json"
MAX_AGE = timedelta(hours=24)
SMARTCTL_TIMEOUT_SECONDS = 30
STANDBY_EXIT_BIT = 2  # with -n standby: "device is in a low-power mode"
_BRIDGE_HINTS = ("unknown usb bridge", "please specify device type", "unsupported usb bridge", "unable to detect device type")
ATTRIBUTES = {5: "reallocated", 197: "pending", 198: "uncorrectable"}

Runner = Callable[..., subprocess.CompletedProcess]


def cache_file() -> Path:
    explicit = os.getenv("AGENT_STATE_DIR")
    if explicit:
        return Path(explicit) / CACHE_FILE_NAME
    systemd = (os.getenv("STATE_DIRECTORY") or "").split(":")[0]
    return Path(systemd or "/var/lib/pbo-agent") / CACHE_FILE_NAME


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _raw(entry: dict[str, Any]) -> int | None:
    raw = entry.get("raw") if isinstance(entry, dict) else None
    value = raw.get("value") if isinstance(raw, dict) else None
    return int(value) if isinstance(value, int) else None


def parse(data: dict[str, Any], *, exit_status: int | None = None) -> dict[str, Any]:
    """smartctl --json output → {"status", "reasons", counters…}."""
    smartctl = data.get("smartctl") if isinstance(data.get("smartctl"), dict) else {}
    code = exit_status if exit_status is not None else int(smartctl.get("exit_status") or 0)
    health = data.get("smart_status") if isinstance(data.get("smart_status"), dict) else None
    result: dict[str, Any] = {"status": "unknown", "reasons": []}
    for item in (data.get("ata_smart_attributes") or {}).get("table") or []:
        name = ATTRIBUTES.get(item.get("id")) if isinstance(item, dict) else None
        if name is not None:
            result[name] = _raw(item)
    nvme = data.get("nvme_smart_health_information_log")
    critical = 0
    if isinstance(nvme, dict):
        if isinstance(nvme.get("media_errors"), int):
            result["media_errors"] = nvme["media_errors"]
        critical = int(nvme.get("critical_warning") or 0)
    temperature = (data.get("temperature") or {}).get("current")
    if isinstance(temperature, int):
        result["temperature_c"] = temperature
    hours = (data.get("power_on_time") or {}).get("hours")
    if isinstance(hours, int):
        result["power_on_hours"] = hours
    if health is None or "passed" not in health:
        result["reasons"] = ["no_smart_data"]
        return result

    reasons: list[str] = []
    danger = health.get("passed") is False or bool(code & 8) or critical != 0
    if health.get("passed") is False or code & 8:
        reasons.append("smart_failed")
    if critical:
        reasons.append("nvme_critical_warning")
    for key, reason in (("reallocated", "reallocated_sectors"), ("pending", "pending_sectors"), ("uncorrectable", "uncorrectable_sectors"), ("media_errors", "media_errors")):
        if (result.get(key) or 0) > 0:
            reasons.append(reason)
    if code & 16:
        reasons.append("prefail_threshold")
    if code & 32:
        reasons.append("past_threshold")
    if code & 64:
        reasons.append("error_log")
    if code & 128:
        reasons.append("selftest_errors")
    result["reasons"] = reasons
    result["status"] = "danger" if danger else ("warning" if reasons else "ok")
    return result


def _run_smartctl(args: list[str], run: Runner) -> tuple[dict[str, Any] | None, int]:
    try:
        completed = run(args, capture_output=True, text=True, timeout=SMARTCTL_TIMEOUT_SECONDS, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("smartctl failed: %s", exc)
        return None, 1
    try:
        data = json.loads(completed.stdout or "{}")
    except ValueError:
        data = None
    return (data if isinstance(data, dict) else None), int(completed.returncode)


def _messages(data: dict[str, Any] | None) -> str:
    items = ((data or {}).get("smartctl") or {}).get("messages") or []
    return " ".join(str(item.get("string", "")) for item in items if isinstance(item, dict)).casefold()


def read_smart(device: str, *, run: Runner = subprocess.run, which: Callable[[str], str | None] = shutil.which) -> dict[str, Any] | None:
    """SMART of /dev/<device>. None = disk asleep (keep the cached value)."""
    if not which("smartctl"):
        return {"status": "unknown", "reasons": ["smartctl_missing"], "message": "smartctl is not installed (smartmontools)."}
    path = f"/dev/{device}"
    base = ["smartctl", "--json=c", "-H", "-A", "-i", "-n", "standby"]
    data, code = _run_smartctl([*base, path], run)
    if code & STANDBY_EXIT_BIT and data is not None and "standby" in _messages(data):
        return None
    if data is None or "smart_status" not in data:
        # USB bridge: try the SCSI/ATA translation layer.
        retry, retry_code = _run_smartctl([*base, "-d", "sat", path], run)
        if retry is not None and "smart_status" in retry:
            data, code = retry, retry_code
        elif retry_code & STANDBY_EXIT_BIT and retry is not None and "standby" in _messages(retry):
            return None
    if data is None or "smart_status" not in data:
        hint = _messages(data)
        result = {"status": "unknown", "reasons": ["usb_bridge_no_smart"]}
        result["message"] = "The USB enclosure does not relay SMART." if any(h in hint for h in _BRIDGE_HINTS) or not hint else hint[:200]
        return result
    return parse(data, exit_status=code)


def load_cache(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_cache(path: Path, cache: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(cache, sort_keys=True), encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        logger.warning("Cannot save the SMART cache (%s).", exc)


def _fresh(entry: dict[str, Any] | None, now: datetime) -> bool:
    if not entry or not entry.get("checked_at"):
        return False
    try:
        checked = datetime.fromisoformat(str(entry["checked_at"]).replace("Z", "+00:00"))
    except ValueError:
        return False
    return now - checked < MAX_AGE


def annotate(
    disks: list[dict[str, Any]],
    *,
    added: set[str],
    path: Path | None = None,
    reader: Callable[[str], dict[str, Any] | None] = read_smart,
    now: datetime | None = None,
) -> None:
    """Adds `smart` to each disk report (reads only plugged-in-now, unknown or
    day-old disks; the others reuse the cache). Disks need an internal
    `_device` key (kernel name), removed by the caller before sending."""
    target = path or cache_file()
    moment = now or _now()
    cache = load_cache(target)
    changed = False
    for disk in disks:
        serial = str(disk.get("serial_number") or "")
        device = disk.get("_device")
        if not serial or not device:
            continue
        entry = cache.get(serial)
        if serial in added or not _fresh(entry, moment):
            result = reader(str(device))
            if result is not None:
                entry = {**result, "checked_at": moment.isoformat().replace("+00:00", "Z")}
                cache[serial] = entry
                changed = True
                if entry["status"] in {"warning", "danger"}:
                    logger.warning("SMART %s for %s: %s", entry["status"], serial, ", ".join(entry.get("reasons") or []))
        if entry:
            disk["smart"] = entry
    if changed:
        save_cache(target, cache)
