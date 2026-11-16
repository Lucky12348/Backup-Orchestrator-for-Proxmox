"""Instant USB disk detection (host role).

A udev rule (90-pbo-agent.rules, installed by install.sh) asks systemd to
start pbo-agent-report.service on every USB disk add/remove. Nothing slow
runs inside udev. The service then:

1. **settles** (debounce): waits ~2 s, then `udevadm settle`, so partitions
   are ready. While it waits the unit is "activating", and systemd merges any
   further start request into that same job: a burst of events (disk +
   partitions, several disks) produces ONE report;
2. sends the usual heartbeat + disk report, plus the USB disks that appeared
   or disappeared since the previous report (`hotplug`, kept for the app's
   history), computed against a small state file.

The 5-minute timer keeps running as a fallback (missed event, agent restart).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

logger = logging.getLogger("agent.hotplug")

DEFAULT_SETTLE_SECONDS = 2.0
UDEVADM_SETTLE_TIMEOUT_SECONDS = 10
STATE_FILE_NAME = "usb-disks.json"


def state_file() -> Path:
    # StateDirectory=pbo-agent in the unit → /var/lib/pbo-agent (STATE_DIRECTORY).
    explicit = os.getenv("AGENT_STATE_DIR")
    if explicit:
        return Path(explicit) / STATE_FILE_NAME
    # systemd may list several directories, colon-separated: take the first.
    systemd = (os.getenv("STATE_DIRECTORY") or "").split(":")[0]
    return Path(systemd or "/var/lib/pbo-agent") / STATE_FILE_NAME


def settle(
    seconds: float = DEFAULT_SETTLE_SECONDS,
    *,
    sleep: Callable[[float], None] = time.sleep,
    run: Callable[..., Any] = subprocess.run,
) -> None:
    """Debounce: let the burst of udev events end and the partitions appear."""
    if seconds > 0:
        sleep(seconds)
    if shutil.which("udevadm"):
        try:
            run(["udevadm", "settle", f"--timeout={UDEVADM_SETTLE_TIMEOUT_SECONDS}"], check=False, timeout=15)
        except (OSError, subprocess.SubprocessError) as exc:
            logger.debug("udevadm settle failed: %s", exc)


def usb_disks(disks: list[dict[str, Any]]) -> dict[str, str | None]:
    return {
        str(disk["serial_number"]): disk.get("model_name")
        for disk in disks
        if disk.get("serial_number") and disk.get("candidate_type") in {"usb", "removable"}
    }


def load_previous(path: Path) -> dict[str, str | None] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def save_current(path: Path, current: dict[str, str | None]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(current, sort_keys=True), encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        logger.warning("Cannot save the USB disk state (%s): hot-plug history may repeat.", exc)


def hotplug_events(previous: dict[str, str | None] | None, current: dict[str, str | None]) -> list[dict[str, Any]]:
    """added/removed USB disks since the previous report. No previous state
    (first run after install) = baseline only, no event."""
    if previous is None:
        return []
    events = [
        {"action": "added", "serial": serial, "model": current[serial]}
        for serial in sorted(set(current) - set(previous))
    ]
    events += [
        {"action": "removed", "serial": serial, "model": previous[serial]}
        for serial in sorted(set(previous) - set(current))
    ]
    return events


def diff_and_store(disks: list[dict[str, Any]], path: Path | None = None) -> list[dict[str, Any]]:
    target = path or state_file()
    current = usb_disks(disks)
    events = hotplug_events(load_previous(target), current)
    save_current(target, current)
    for event in events:
        logger.info("USB disk %s: %s (%s)", event["action"], event["serial"], event.get("model") or "?")
    return events
