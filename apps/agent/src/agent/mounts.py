"""Stale mounts of a removable datastore inside the PBS VM (host role, B41).

When a removable datastore's disk is pulled out, its ext4 mount stays in
place in the PBS VM with the device gone (`shutdown` option). PBS no longer
counts it as mounted, so the next mount stacks on top (`/dev/sdc1` then
`/dev/sdd1` on the same path). The PBS REST API can neither list the mounts of
a path nor detach one lazily.

PBS runs in a VM of this node (the one receiving the USB passthrough): the
host agent works inside it through the QEMU Guest Agent (`qm guest exec`,
root on the host — no extra Proxmox privilege for the PBO token):
`cat /proc/self/mountinfo`, `[ -e <device> ]`, `umount -l <path>`.

Only DEAD layers are removed (device gone, or filesystem shut down), from the
top of the stack, with `umount -l`; a live mount is never touched; dead layers
under a live one are reported. Paths under /mnt/datastore only.
"""

from __future__ import annotations

import json
import posixpath
import subprocess
from collections.abc import Callable
from typing import Any

DATASTORE_ROOT = "/mnt/datastore"
MAX_LAYERS = 16
GUEST_TIMEOUT_SECONDS = 30
_DEVICE_CHECK = 'for d; do [ -e "$d" ] && echo "$d"; done'

Runner = Callable[..., Any]


class GuestAgentUnavailable(RuntimeError):
    """The QEMU guest agent of the VM does not answer."""


def _unescape(value: str) -> str:
    return value.replace("\\040", " ").replace("\\011", "\t").replace("\\012", "\n").replace("\\134", "\\")


def validate_path(path: str) -> str:
    normalized = posixpath.normpath(path or "")
    if not normalized.startswith(DATASTORE_ROOT + "/") or normalized.count("/") != 3:
        raise ValueError(f"Only {DATASTORE_ROOT}/<datastore> paths are handled, not `{path}`.")
    return normalized


def guest_exec(vmid: int, argv: list[str], *, run: Runner = subprocess.run) -> dict[str, Any]:
    """`qm guest exec <vmid> -- argv` → {"exitcode", "out-data", "err-data"}."""
    command = ["qm", "guest", "exec", str(int(vmid)), "--timeout", str(GUEST_TIMEOUT_SECONDS), "--", *argv]
    completed = run(command, capture_output=True, text=True, check=False, timeout=GUEST_TIMEOUT_SECONDS + 15)
    if completed.returncode != 0:
        raise GuestAgentUnavailable((completed.stderr or completed.stdout or "qm guest exec failed").strip())
    try:
        data = json.loads(completed.stdout or "{}")
    except ValueError as exc:
        raise GuestAgentUnavailable(f"Unexpected `qm guest exec` output: {completed.stdout!r}") from exc
    return data if isinstance(data, dict) else {}


def guest_agent_status(vmid: int, *, run: Runner = subprocess.run) -> dict[str, Any]:
    """Is the QEMU guest agent enabled in the VM config and answering?"""
    config = run(["qm", "config", str(int(vmid))], capture_output=True, text=True, check=False, timeout=30)
    agent_line = next((line for line in (config.stdout or "").splitlines() if line.startswith("agent:")), "")
    value = agent_line.split(":", 1)[1].strip() if agent_line else ""
    enabled = value.startswith("1") or "enabled=1" in value
    ping = run(["qm", "guest", "cmd", str(int(vmid)), "ping"], capture_output=True, text=True, check=False, timeout=30)
    active = ping.returncode == 0
    return {
        "ok": True,
        "success": True,
        "vmid": int(vmid),
        "enabled": enabled,
        "active": active,
        "message": "QEMU guest agent active." if active else (ping.stderr or "QEMU guest agent not answering.").strip(),
    }


def mounts_at(path: str, mountinfo: str, existing: set[str]) -> list[dict[str, Any]]:
    """Mounts on exactly `path`, bottom first (mountinfo order)."""
    found: list[dict[str, Any]] = []
    for line in mountinfo.splitlines():
        left, _, right = line.partition(" - ")
        fields, tail = left.split(), right.split()
        if len(fields) < 6 or len(tail) < 2 or _unescape(fields[4]) != path:
            continue
        source = _unescape(tail[1])
        options = ",".join(filter(None, [fields[5], tail[2] if len(tail) > 2 else ""]))
        device_exists = source in existing if source.startswith("/dev/") else True
        shutdown = "shutdown" in options.split(",")
        found.append(
            {
                "source": source,
                "fstype": tail[0],
                "options": options,
                "device_exists": device_exists,
                "shutdown": shutdown,
                "dead": (not device_exists) or shutdown,
            }
        )
    return found


def guest_mounts(vmid: int, path: str, *, run: Runner = subprocess.run) -> list[dict[str, Any]]:
    mountinfo = str(guest_exec(vmid, ["cat", "/proc/self/mountinfo"], run=run).get("out-data") or "")
    raw = [line for line in mountinfo.splitlines() if f" {path} " in line]
    sources = sorted({_unescape(line.partition(" - ")[2].split()[1]) for line in raw if len(line.partition(" - ")[2].split()) > 1})
    devices = [source for source in sources if source.startswith("/dev/")]
    existing: set[str] = set()
    if devices:
        checked = guest_exec(vmid, ["sh", "-c", _DEVICE_CHECK, "sh", *devices], run=run)
        existing = {line.strip() for line in str(checked.get("out-data") or "").splitlines() if line.strip()}
    return mounts_at(path, mountinfo, existing)


def _result(path: str, removed: list[dict[str, Any]], remaining: list[dict[str, Any]], *, error: str | None = None) -> dict[str, Any]:
    top = remaining[-1] if remaining else None
    return {
        "ok": error is None,
        "success": error is None,
        "guest_agent": True,
        "path": path,
        "removed": removed,
        "remaining": remaining,
        "live_mount": bool(top and not top["dead"]),
        "hidden_dead": sum(1 for item in remaining[:-1] if item["dead"]) if top and not top["dead"] else 0,
        "error": error,
        "message": f"{len(removed)} stale mount(s) removed from {path}." if error is None else f"Lazy unmount of {path} failed: {error}",
    }


def cleanup_dead_mounts(vmid: int, path: str, *, run: Runner = subprocess.run) -> dict[str, Any]:
    target = validate_path(path)
    try:
        removed: list[dict[str, Any]] = []
        for _ in range(MAX_LAYERS):
            stack = guest_mounts(vmid, target, run=run)
            if not stack or not stack[-1]["dead"]:
                break
            top = stack[-1]
            result = guest_exec(vmid, ["umount", "-l", target], run=run)
            if int(result.get("exitcode") or 0) != 0:
                return _result(target, removed, guest_mounts(vmid, target, run=run), error=str(result.get("err-data") or "umount -l failed").strip())
            removed.append({"source": top["source"], "reason": "device_gone" if not top["device_exists"] else "shutdown"})
        return _result(target, removed, guest_mounts(vmid, target, run=run))
    except GuestAgentUnavailable as exc:
        return {"ok": True, "success": True, "guest_agent": False, "path": target, "removed": [], "remaining": [], "live_mount": False, "message": str(exc)}
