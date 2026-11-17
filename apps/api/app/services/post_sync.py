"""After the sync, inside the same run (Phase 5f): Cleanup, contents, Verification.

  sync_job → cleanup (prune-datastore with the disk's retention, then garbage
  collection) → contents (snapshots of the disk, cached for the disk sheet)
  → verify (PBS verify of the snapshots this run copied, when asked) → eject.

A failed cleanup is a warning (the copy is there); a failed verification
fails the run (the copy cannot be trusted). These steps need the PBS REST API
(PBS_MODE=api); with the legacy PBS agent they are skipped with a journal line.

Verify-only run (proposed after a removal without eject): hand-off → reuse the
datastore (never format) → verify everything → contents → eject.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import ExternalBackupRun, ExternalDisk
from app.services import disk_contents, disk_profile
from app.services.external_backup_agent import AgentCommandError

Log = Callable[..., Any]


class VerifyFailed(RuntimeError):
    code = "verify_failed"


def api_mode() -> bool:
    return get_settings().pbs_mode == "api"


def cleanup(bridge: Any, run: ExternalBackupRun, disk: ExternalDisk, store: str, log: Log) -> None:
    if not api_mode():
        log(step="cleanup", message="Retention needs PBS_MODE=api: nothing was pruned on the disk.")
        return
    keep = disk_profile.prune_options(disk_profile.retention_of(disk))
    try:
        result = bridge.pbs_command("/prune-datastore", {"datastore_name": store, "keep": keep}, run_id=run.id)
        log(step="cleanup", message=result.message)
    except AgentCommandError as exc:
        code = (exc.payload or {}).get("error_code") if isinstance(getattr(exc, "payload", None), dict) else None
        if code == "gc_failed":
            log(step="cleanup", message=f"Garbage collection warning: {exc}", stream="stderr")
        else:
            log(step="cleanup", message=f"Retention warning: {exc}", stream="stderr")


def refresh_contents(db: Session, bridge: Any, run: ExternalBackupRun | None, disk: ExternalDisk, store: str, log: Log | None) -> list[str] | None:
    """Reads the disk's snapshots in PBS, caches them; returns their keys."""
    if not api_mode():
        return None
    try:
        result = bridge.pbs_command("/list-datastore-snapshots", {"datastore_name": store}, run_id=None)
    except AgentCommandError:
        return None
    snapshots = (result.payload or {}).get("snapshots") or []
    disk_contents.store_contents(db, disk, snapshots)
    db.commit()
    cache = disk.contents_cache or {}
    if log is not None:
        versions = sum(int(item.get("versions") or 0) for item in cache.get("items") or [])
        log(step="verify" if run is not None and run.verify_only else "cleanup", message=f"Disk contents updated: {len(cache.get('items') or [])} VM/CT, {versions} version(s).")
    return list(cache.get("snapshots") or [])


def verify(
    bridge: Any,
    run: ExternalBackupRun,
    store: str,
    log: Log,
    *,
    copied: list[str] | None,
    verify_all: bool = False,
) -> None:
    if not api_mode():
        log(step="verify", message="Verification needs PBS_MODE=api: skipped.")
        return
    if not verify_all and not copied:
        log(step="verify", message="Verification skipped: no new version was copied.")
        return
    payload: dict[str, Any] = {"datastore_name": store}
    if verify_all:
        payload["all"] = True
    else:
        payload["snapshots"] = copied
    try:
        result = bridge.pbs_command("/verify-snapshots", payload, run_id=run.id)
    except AgentCommandError as exc:
        log(step="verify_failed", message=f"PBS verify failed: {exc}", stream="stderr")
        raise VerifyFailed(f"PBS verify failed: {exc}") from exc
    log(step="verify", message=result.message)


def copied_keys(before: list[str] | None, after: list[str] | None) -> tuple[list[str] | None, bool]:
    """Snapshots copied by this run, still there after the cleanup. Unknown
    "before" → verify everything (`True`)."""
    if after is None:
        return None, True  # the disk's snapshots could not be read: verify everything
    if before is None:
        return None, True
    known = set(before)
    return [key for key in after if key not in known], False
