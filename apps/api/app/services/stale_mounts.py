"""Stale mounts stacked on a removable datastore path in the PBS VM (B41).

A disk pulled out leaves its ext4 mount behind (device gone, `shutdown`).
PBS's mount check does not see such a layer (`mount-status` says
`notmounted`) and mounts the next disk on top: /dev/sdc1 then /dev/sdd1 on the
same path. The PBS REST API can neither list the mounts of a path nor
unmount lazily.

PBS runs in a VM of the Proxmox node (the one receiving the USB passthrough),
so the HOST agent works inside it through the QEMU Guest Agent
(`qm guest exec`, routes `/pbs-guest/agent` and `/pbs-guest/mounts/cleanup`,
capability `pbs-guest-mounts`, `apps/agent/src/agent/mounts.py`): dead layers
removed from the top with `umount -l`, never a live one. No PBS agent, no
extra Proxmox privilege for the PBO token (`qm` runs as root on the host).

Without a guest agent in the PBS VM: no check (journal line), a non-blocking
readiness warning with the commands to enable it (`guest_agent_commands`).
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

DATASTORE_ROOT = "/mnt/datastore"
STATUS_CACHE_SECONDS = 60.0
_lock = threading.Lock()
_status_cache: dict[int, tuple[float, dict[str, Any] | None]] = {}


# Without a subscription the PBS enterprise repository answers 401 and apt
# fails: disabled first, in both formats (PBS 4: deb822 `.sources` gets
# `Enabled: no` in each stanza; PBS 3: `.list` lines commented out).
DISABLE_PBS_ENTERPRISE = (
    "for f in /etc/apt/sources.list.d/pbs-enterprise.*; do case \"$f\" in "
    "*.sources) sed -i '/^Enabled:/d; /^Types:/a Enabled: no' \"$f\" ;; "
    "*.list) sed -i 's/^deb /# deb /' \"$f\" ;; esac; done"
)


def guest_agent_commands(vmid: int) -> list[dict[str, str]]:
    """What to run when the PBS VM has no QEMU guest agent: in the PBS VM,
    then on the Proxmox host (`where`)."""
    return [
        {"where": "pbs", "command": DISABLE_PBS_ENTERPRISE},
        {"where": "pbs", "command": "apt update && apt install -y qemu-guest-agent"},
        {"where": "host", "command": f"qm set {vmid} --agent enabled=1"},
        {"where": "host", "command": f"qm shutdown {vmid} && qm start {vmid}"},
    ]


def _host(settings: Any, client: Any | None) -> Any:
    if client is not None:
        return client
    from app.services.host_agent import get_host_agent_client

    return get_host_agent_client()


def guest_agent_status(settings: Any, *, client: Any | None = None, use_cache: bool = True) -> dict[str, Any] | None:
    """{"enabled", "active"} of the PBS VM's guest agent; None = cannot tell
    (no PBS VM set, host agent too old or unreachable)."""
    vmid = int(settings.pbs_execution_vm_id or 0)
    if vmid <= 0 or not settings.host_agent_base_url and client is None:
        return None
    now = time.monotonic()
    if use_cache:
        with _lock:
            cached = _status_cache.get(vmid)
            if cached and now - cached[0] < STATUS_CACHE_SECONDS:
                return cached[1]
    try:
        payload = _host(settings, client).post("/pbs-guest/agent", {"vmid": vmid}).payload or {}
        value: dict[str, Any] | None = {"enabled": bool(payload.get("enabled")), "active": bool(payload.get("active")), "vmid": vmid}
    except Exception as exc:  # noqa: BLE001
        logger.info("PBS VM guest agent status unavailable: %s", exc)
        value = None
    with _lock:
        _status_cache[vmid] = (now, value)
    return value


def reset_cache() -> None:
    with _lock:
        _status_cache.clear()


def clean(store: str, *, settings: Any, client: Any | None = None, log: Callable[[str], None] | None = None) -> dict[str, Any] | None:
    """Removes the dead layers on /mnt/datastore/<store> inside the PBS VM.
    None = could not check (no guest agent, old host agent, unreachable)."""
    vmid = int(settings.pbs_execution_vm_id or 0)
    if vmid <= 0:
        return None
    path = f"{DATASTORE_ROOT}/{store}"
    try:
        result = _host(settings, client).post("/pbs-guest/mounts/cleanup", {"vmid": vmid, "path": path})
    except Exception as exc:  # noqa: BLE001 — old host agent (no route) or unreachable
        logger.info("Stale mount check of %s unavailable: %s", path, exc)
        return None
    payload = result.payload if isinstance(getattr(result, "payload", None), dict) else {}
    if not payload.get("guest_agent", False):
        return None
    for item in payload.get("removed") or []:
        reason = "device gone" if item.get("reason") == "device_gone" else "filesystem shut down"
        message = f"Stale mount removed from {path}: {item.get('source')} ({reason}), lazy unmount."
        logger.warning(message)
        if log is not None:
            log(message)
    return payload
