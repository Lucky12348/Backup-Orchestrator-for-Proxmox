"""Stale mounts of a removable datastore (PBS role, Phase 5f.3 / B41).

When a removable datastore's disk is pulled out, its ext4 mount stays in
place with the device gone (`shutdown` option). PBS no longer counts it as
mounted (its mount check reads the filesystem UUID of the mount source), so
the next mount stacks on top: `/dev/sdc1` then `/dev/sdd1` on the same path.
The PBS REST API can neither list the mounts of a path nor detach one lazily;
this module does both, for paths under /mnt/datastore only.

Only DEAD layers are removed (device gone, or filesystem shut down), from the
top of the stack, with `umount -l` (lazy: never blocks on a dead device). A
live mount on top is never touched; dead layers under it are reported.
"""

from __future__ import annotations

import os
import posixpath
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

DATASTORE_ROOT = "/mnt/datastore"
MOUNTINFO = Path("/proc/self/mountinfo")
MAX_LAYERS = 16


def _unescape(value: str) -> str:
    return value.replace("\\040", " ").replace("\\011", "\t").replace("\\012", "\n").replace("\\134", "\\")


def validate_path(path: str) -> str:
    normalized = posixpath.normpath(path or "")
    if not normalized.startswith(DATASTORE_ROOT + "/") or normalized.count("/") != 3:
        raise ValueError(f"Only {DATASTORE_ROOT}/<datastore> paths are handled, not `{path}`.")
    return normalized


def mounts_at(path: str, *, mountinfo: Path = MOUNTINFO, exists: Callable[[str], bool] = os.path.exists) -> list[dict[str, Any]]:
    """Mounts on exactly `path`, bottom first (mountinfo order)."""
    found: list[dict[str, Any]] = []
    try:
        lines = mountinfo.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return found
    for line in lines:
        left, _, right = line.partition(" - ")
        fields, tail = left.split(), right.split()
        if len(fields) < 6 or len(tail) < 2:
            continue
        if _unescape(fields[4]) != path:
            continue
        source = _unescape(tail[1])
        options = ",".join(filter(None, [fields[5], tail[2] if len(tail) > 2 else ""]))
        device_exists = exists(source) if source.startswith("/dev/") else True
        shutdown = "shutdown" in options.split(",")
        found.append(
            {
                "mount_id": fields[0],
                "source": source,
                "fstype": tail[0],
                "options": options,
                "device_exists": device_exists,
                "shutdown": shutdown,
                "dead": (not device_exists) or shutdown,
            }
        )
    return found


def cleanup_dead_mounts(
    path: str,
    *,
    run: Callable[..., Any] = subprocess.run,
    mountinfo: Path = MOUNTINFO,
    exists: Callable[[str], bool] = os.path.exists,
) -> dict[str, Any]:
    target = validate_path(path)
    removed: list[dict[str, Any]] = []
    for _ in range(MAX_LAYERS):
        stack = mounts_at(target, mountinfo=mountinfo, exists=exists)
        if not stack or not stack[-1]["dead"]:
            break
        top = stack[-1]
        completed = run(["umount", "-l", target], capture_output=True, text=True, check=False, timeout=30)
        if getattr(completed, "returncode", 1) != 0:
            return _result(target, removed, mounts_at(target, mountinfo=mountinfo, exists=exists), error=(completed.stderr or "").strip() or "umount -l failed")
        removed.append({"source": top["source"], "reason": "device_gone" if not top["device_exists"] else "shutdown"})
    return _result(target, removed, mounts_at(target, mountinfo=mountinfo, exists=exists))


def _result(path: str, removed: list[dict[str, Any]], remaining: list[dict[str, Any]], *, error: str | None = None) -> dict[str, Any]:
    top = remaining[-1] if remaining else None
    return {
        "ok": error is None,
        "success": error is None,
        "path": path,
        "removed": removed,
        "remaining": remaining,
        "live_mount": bool(top and not top["dead"]),
        "hidden_dead": sum(1 for item in remaining[:-1] if item["dead"]) if top and not top["dead"] else 0,
        "error": error,
        "message": f"{len(removed)} stale mount(s) removed from {path}." if error is None else f"Lazy unmount of {path} failed: {error}",
    }


def mounts_result(path: str) -> dict[str, Any]:
    target = validate_path(path)
    stack = mounts_at(target)
    return _result(target, [], stack)
