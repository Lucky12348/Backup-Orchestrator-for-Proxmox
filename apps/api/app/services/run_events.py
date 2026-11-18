"""run.progress / run.log / run.finished, published from SQLAlchemy hooks.

Backups and ejects write their state through many paths (agent callbacks,
the PBS API backend, the eject flow, the scheduler). Instead of touching each
one, the changes are collected at flush time (attribute history) and
published once the transaction commits — a rolled-back change is never sent.

Also: agent.online / agent.offline transitions (heartbeat + a small watcher).
"""

from __future__ import annotations

import asyncio
import logging
import threading
from contextlib import suppress
from datetime import datetime
from typing import Any

from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

from app.models.backup_run import BackupRunStatus
from app.models.disk_preparation_run import DiskPreparationRun
from app.models.external_backup_run import ExternalBackupRun
from app.services import events

logger = logging.getLogger(__name__)

PROGRESS_FIELDS = (
    "status",
    "current_step",
    "progress_message",
    "progress_percent",
    "total_groups",
    "completed_groups",
    "current_group",
    "current_snapshot",
    "current_archive",
    "downloaded_bytes",
    "current_speed",
    "elapsed_seconds",
)
FINISHED = {BackupRunStatus.SUCCESS, BackupRunStatus.FAILED}
MAX_LOG_LINES_PER_EVENT = 200
_PENDING_KEY = "pbo_run_events"


def _kind(obj: Any) -> str | None:
    if isinstance(obj, ExternalBackupRun):
        return "external_backup"
    if isinstance(obj, DiskPreparationRun):
        return "disk_preparation"
    return None


def _value(value: Any) -> Any:
    if isinstance(value, BackupRunStatus):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def new_log_lines(old: str | None, new: str | None) -> list[str]:
    """Lines appended to a log (logs only grow; a rewrite sends the tail)."""
    if not new or new == old:
        return []
    added = new[len(old):] if old and new.startswith(old) else new
    lines = [line for line in added.splitlines() if line.strip()]
    return lines[-MAX_LOG_LINES_PER_EVENT:]


def run_events_for(obj: Any, *, is_new: bool) -> list[tuple[str, dict[str, Any]]]:
    kind = _kind(obj)
    if kind is None or obj.id is None:
        return []
    state = inspect(obj)
    attrs = state.attrs
    base = {"run_id": obj.id, "kind": kind, "disk_id": getattr(obj, "disk_id", None)}

    def changed(name: str) -> bool:
        return name in attrs.keys() and (is_new or attrs[name].history.has_changes())

    out: list[tuple[str, dict[str, Any]]] = []
    if any(changed(name) for name in PROGRESS_FIELDS):
        out.append(
            (
                "run.progress",
                {
                    **base,
                    **{name: _value(getattr(obj, name, None)) for name in PROGRESS_FIELDS if name in attrs.keys()},
                    "mode": _value(getattr(obj, "mode", None)),
                },
            )
        )
    for stream in ("stdout_log", "stderr_log"):
        if stream in attrs.keys() and changed(stream):
            history = attrs[stream].history
            old = history.deleted[0] if history.deleted else None
            lines = new_log_lines(old, getattr(obj, stream))
            if lines:
                out.append(("run.log", {**base, "stream": stream.removesuffix("_log"), "lines": lines}))
    if changed("status") and obj.status in FINISHED:
        out.append(
            (
                "run.finished",
                {
                    **base,
                    "status": _value(obj.status),
                    "message": getattr(obj, "message", None),
                    "current_step": getattr(obj, "current_step", None),
                    "finished_at": _value(getattr(obj, "finished_at", None)),
                },
            )
        )
    return out


@event.listens_for(Session, "after_flush")
def _collect(session: Session, _flush_context: Any) -> None:
    pending = session.info.setdefault(_PENDING_KEY, [])
    for obj in session.new:
        with suppress(Exception):
            pending.extend(run_events_for(obj, is_new=True))
    for obj in session.dirty:
        with suppress(Exception):
            pending.extend(run_events_for(obj, is_new=False))


@event.listens_for(Session, "after_commit")
def _publish(session: Session) -> None:
    pending = session.info.pop(_PENDING_KEY, [])
    for event_type, data in pending:
        events.publish(event_type, data)


@event.listens_for(Session, "after_rollback")
def _discard(session: Session) -> None:
    session.info.pop(_PENDING_KEY, None)


# --- Agent presence -----------------------------------------------------------

_presence_lock = threading.Lock()
_agent_connected: bool | None = None


def update_agent_presence(connected: bool, hostname: str | None = None) -> None:
    """Publish agent.online / agent.offline on transitions only."""
    global _agent_connected
    with _presence_lock:
        previous = _agent_connected
        _agent_connected = connected
    if previous is None and not connected:
        return  # startup with no agent: nothing went offline
    if previous != connected:
        events.publish("agent.online" if connected else "agent.offline", {"hostname": hostname})
        if not connected:
            from app.services import notifications

            notifications.agent_offline("host", hostname)


def reset_agent_presence() -> None:
    global _agent_connected
    with _presence_lock:
        _agent_connected = None


async def watch_agent_presence(session_factory, interval_seconds: float = 30.0) -> None:
    """Detects silence (agent.offline): heartbeats only tell when it is back."""
    from app.services.disks import get_agent_status

    while True:
        try:
            with session_factory() as db:
                status = get_agent_status(db)
            update_agent_presence(bool(status.get("connected")), status.get("hostname"))  # type: ignore[arg-type]
        except Exception:
            logger.debug("Agent presence check failed", exc_info=True)
        await asyncio.sleep(interval_seconds)
