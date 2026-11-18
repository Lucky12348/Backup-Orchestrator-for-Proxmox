"""Where PBO's code says "this happened": thin hooks over the notification
engine (services/notification_engine.py, Phase 5j). Each hook picks the event
of services/notification_catalog.py, its parameters and dedup key; channels,
levels, quiet hours, digest and retries are the engine's business.

Every hook is safe to call from any code path: it never raises.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.services.notification_engine import emit


def _duration(started: datetime | None, finished: datetime | None) -> str:
    if not started or not finished:
        return ""
    seconds = max(0, int((finished - started).total_seconds()))
    if seconds < 60:
        return f"{seconds} s"
    minutes = round(seconds / 60)
    return f"{minutes} min" if minutes < 60 else f"{minutes // 60} h {minutes % 60:02d}"


def _short(text: str | None, limit: int = 200) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def run_finished(run: Any, disk: Any) -> None:
    """End of a backup, verification or repair run (external_backups)."""
    status = getattr(getattr(run, "status", None), "value", getattr(run, "status", None))
    status = str(status or "").lower()
    if getattr(run, "interrupted", None):
        emit("run_interrupted", disk=disk, dedup=f"run:{run.id}")
        return
    if status == "success":
        if getattr(run, "repair", False):
            emit("repair_completed", disk=disk, dedup=f"run:{run.id}")
        elif not getattr(run, "verify_only", False):
            emit("run_success", disk=disk, dedup=f"run:{run.id}", params={"detail": _duration(run.started_at, run.finished_at)})
        return
    if status != "failed":
        return
    if getattr(run, "verify_only", False) or run.current_step == "verify_failed":
        emit("verify_failed", disk=disk, dedup=f"run:{run.id}")
    else:
        emit("run_failed", disk=disk, dedup=f"run:{run.id}", params={"detail": _short(run.message) or run.current_step})


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
        params={"status": status, "detail": _short(detail)},
        dedup=f"{getattr(disk, 'id', '')}:{status}",
    )


def disk_new(disk: Any) -> None:
    emit("disk_new", disk=disk)


def agent_offline(role: str, host: str | None) -> None:
    emit("agent_offline", params={"role": role, "host": host or role}, dedup=f"{role}:{host}")


def agent_update_failed(role: str, host: str | None, detail: str | None) -> None:
    emit("agent_update_failed", params={"role": role, "host": host or role, "detail": _short(detail)}, dedup=f"{role}:{host}")


def integration_unreachable(target: str, detail: str | None) -> None:
    """target: "pve" | "pbs"."""
    emit("pve_unreachable" if target == "pve" else "pbs_unreachable", params={"detail": _short(detail)}, dedup=target)


def guest_agent_missing() -> None:
    emit("guest_agent_missing", dedup="pbs")
