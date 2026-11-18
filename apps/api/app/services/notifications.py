"""Where PBO's code says "this happened": thin hooks over the notification
engine (services/notification_engine.py, Phase 5j). Each hook picks the event
of services/notification_catalog.py, its parameters and dedup key; channels,
levels, quiet hours, digest, retries and the inbox are the engine's business.

B55: parameters are numbers, names, or `catalog.term()`s (both languages),
never free English text from PVE / PBS / an agent: messages follow PBO's
language from the first word to the last.

Every hook is safe to call from any code path: it never raises.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from app.services import notification_catalog as catalog
from app.services.notification_engine import emit

_JOURNAL_STEP = re.compile(r"^\[[^\]]+\] ([a-z][a-z0-9_]*)\b", re.MULTILINE)
# Journal step prefix → stage of the run that failed.
_STAGES: tuple[tuple[str, str], ...] = (
    ("handoff", "passthrough"), ("usb", "passthrough"), ("passthrough", "passthrough"), ("detect", "passthrough"),
    ("prepare", "mount"), ("mount", "mount"), ("datastore", "mount"), ("stale", "mount"), ("pbs_config", "mount"),
    ("sync", "sync"), ("run_export", "sync"), ("export", "sync"), ("repair", "sync"), ("delete", "sync"),
    ("cleanup", "cleanup"), ("prune", "cleanup"), ("gc", "cleanup"), ("contents", "cleanup"),
    ("verify", "verify"),
    ("release", "release"), ("eject", "release"),
    ("disk_", "readiness"), ("source_datastore", "readiness"), ("smart", "readiness"), ("insufficient", "readiness"),
)


def _duration(started: datetime | None, finished: datetime | None) -> str:
    if not started or not finished:
        return ""
    seconds = max(0, int((finished - started).total_seconds()))
    if seconds < 60:
        return f"{seconds} s"
    minutes = round(seconds / 60)
    return f"{minutes} min" if minutes < 60 else f"{minutes // 60} h {minutes % 60:02d}"


def _stage_of(step: str) -> str | None:
    for prefix, stage in _STAGES:
        if step.startswith(prefix):
            return stage
    return None


def failure_stage(run: Any) -> str:
    """Where a failed run stopped: its coded step, else the last journal step."""
    step = str(getattr(run, "current_step", "") or "")
    if step == "verify_failed":
        return "verify"
    stage = _stage_of(step)
    if stage:
        return stage
    for previous in reversed(_JOURNAL_STEP.findall(getattr(run, "stdout_log", None) or "")):
        stage = _stage_of(previous)
        if stage:
            return stage
    return "unknown"


def _kind(run: Any) -> str:
    if getattr(run, "repair", False):
        return "repair"
    return "verify" if getattr(run, "verify_only", False) else "backup"


def run_finished(run: Any, disk: Any) -> None:
    """End of a backup, verification or repair run (external_backups)."""
    from app.services.run_control import CANCELLED_STEP

    status = getattr(getattr(run, "status", None), "value", getattr(run, "status", None))
    status = str(status or "").lower()
    kind = catalog.RUN_KINDS[_kind(run)]
    dedup = f"run:{run.id}"
    if getattr(run, "current_step", None) == CANCELLED_STEP:
        released = not getattr(disk, "pbs_handoff_slot", None)
        reason = catalog.STOP_REASONS["cancelled" if released else "cancelled_not_released"]
        emit("run_interrupted", disk=disk, dedup=dedup, level="warning", params={"kind": kind, "detail": reason})
        return
    if getattr(run, "interrupted", None):
        reason = catalog.STOP_REASONS.get(run.interrupted, catalog.STOP_REASONS["during_sync"])
        emit("run_interrupted", disk=disk, dedup=dedup, params={"kind": kind, "detail": reason})
        return
    if status == "success":
        if getattr(run, "repair", False):
            emit("repair_completed", disk=disk, dedup=dedup)
        elif not getattr(run, "verify_only", False):
            emit("run_success", disk=disk, dedup=dedup, params={"detail": _duration(run.started_at, run.finished_at)})
        return
    if status != "failed":
        return
    stage = failure_stage(run)
    if getattr(run, "verify_only", False) or stage == "verify":
        emit("verify_failed", disk=disk, dedup=dedup)
    else:
        emit("run_failed", disk=disk, dedup=dedup, params={"kind": kind, "detail": catalog.FAILED_STAGES[stage]})


def unsafe_removal(disk: Any) -> None:
    emit("unsafe_removal", disk=disk)


def disk_to_plug(disk: Any, planning: str, at: str, *, disk_label: str | None = None) -> None:
    emit("disk_to_plug", disk=disk, params={"planning": planning, "time": at, **({"disk": disk_label} if disk is None and disk_label else {})}, dedup=f"{planning}:{at}")


def rotation_missed(disk: Any, planning: str, *, disk_label: str | None = None, occurrence: str = "") -> None:
    emit("rotation_missed", disk=disk, params={"planning": planning, **({"disk": disk_label} if disk is None and disk_label else {})}, dedup=f"{planning}:{occurrence}")


def backup_stale(disk: Any, days: int) -> None:
    emit("backup_stale", disk=disk, params={"days": days})


def disk_space(disk: Any, level_percent: int, used_gb: int, total_gb: int) -> None:
    emit(
        "disk_space",
        disk=disk,
        level="error" if level_percent >= 90 else "warning",
        params={"percent": level_percent, "used": used_gb, "total": total_gb},
        dedup=f"{getattr(disk, 'id', '')}:{level_percent}",
    )


def smart_degraded(disk: Any, status: str, detail: str | None = None) -> None:
    emit(
        "smart_degraded",
        disk=disk,
        level="error" if status == "danger" else "warning",
        params={"status": catalog.SMART_STATES.get(status, catalog.SMART_STATES["warning"])},
        dedup=f"{getattr(disk, 'id', '')}:{status}",
    )


def disk_new(disk: Any) -> None:
    emit("disk_new", disk=disk)


def _role(role: str) -> Any:
    return catalog.AGENT_ROLES.get(role, role)


def agent_offline(role: str, host: str | None) -> None:
    emit("agent_offline", params={"role": _role(role), "host": host or role}, dedup=f"{role}:{host}")


def agent_update_failed(role: str, host: str | None, result: str | None) -> None:
    """result: the update's final status (rolled_back | failed | too_old | cancelled)."""
    detail = catalog.UPDATE_RESULTS.get(result or "", catalog.UPDATE_RESULTS["failed"])
    emit("agent_update_failed", params={"role": _role(role), "host": host or role, "detail": detail}, dedup=f"{role}:{host}")


def integration_unreachable(target: str, url: str | None = None) -> None:
    """target: "pve" | "pbs"; url: its configured address (only the host is shown)."""
    host = urlsplit(url or "").hostname if url else None
    emit("pve_unreachable" if target == "pve" else "pbs_unreachable", params={"host": f" ({host})" if host else ""}, dedup=target)


def guest_agent_missing() -> None:
    emit("guest_agent_missing", dedup="pbs")
