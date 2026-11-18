"""Self-update of the registered agents (Phase 5g).

The app ships one agent build (`agent_bootstrap.shipped_agent()`: version,
build id = hash of apps/agent sources, SHA-256 of the archive). Each agent
announces its build on every heartbeat and on `/version`; when it is older:

1. `GET /version`: an agent without the `self-update` capability is too old
   to update itself → `too_old` (the UI shows the install command once more);
2. `POST /self-update {build_id, sha256}`: the agent downloads the archive
   from THIS app only, over its pinned TLS connection, checks the SHA-256,
   installs atomically and restarts, keeping its tokens (agent/self_update.py);
3. `GET /self-update/status` is polled until the agent answers with the new
   build (`success`), or says it rolled back (`rolled_back`, alert), or never
   answers again (`failed`, alert).

Never while a backup/verification/repair runs (`run_active`): restarting the
host agent mid-run would break the USB passthrough steps. Auto updates
(heartbeat, end of the LXC `update`) follow AUTO_UPDATE_AGENTS and are not
retried for a build whose update already failed; "Update now" always tries.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import SecretDecryptionError, decrypt_value
from app.models import ActivityEvent, AgentUpdate, BackupRunStatus, ExternalBackupRun, RegisteredAgent
from app.services.agent_bootstrap import shipped_agent

logger = logging.getLogger(__name__)

TRIGGERS = ("auto", "manual", "update_command")
FINAL = {"success", "up_to_date", "rolled_back", "failed", "too_old", "busy"}
APPLY_TIMEOUT_SECONDS = 240.0
POLL_SECONDS = 3.0
STALE_RUNNING = timedelta(minutes=10)
HISTORY_LIMIT = 20


class AgentUpdateError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def active_agents(db: Session) -> list[RegisteredAgent]:
    return list(db.scalars(select(RegisteredAgent).where(RegisteredAgent.revoked_at.is_(None)).order_by(RegisteredAgent.id)))


def record_announced(db: Session, agent: RegisteredAgent, version: str | None, build_id: str | None) -> None:
    agent.agent_version = (version or None) and str(version)[:64]
    agent.build_id = (build_id or None) and str(build_id)[:64]
    agent.version_seen_at = _utcnow()
    db.add(agent)
    db.commit()


def _running(db: Session, agent_id: int) -> AgentUpdate | None:
    row = db.scalar(
        select(AgentUpdate).where(AgentUpdate.agent_id == agent_id, AgentUpdate.status == "running").order_by(AgentUpdate.id.desc()).limit(1)
    )
    if row is not None and _utcnow() - (_aware(row.started_at) or _utcnow()) > STALE_RUNNING:
        _finish(db, row, "failed", "update_timeout", "The update never reported its end.")
        return None
    return row


def run_active(db: Session) -> bool:
    return (
        db.scalar(
            select(ExternalBackupRun.id).where(ExternalBackupRun.status.in_([BackupRunStatus.PENDING, BackupRunStatus.RUNNING])).limit(1)
        )
        is not None
    )


def agent_state(db: Session, agent: RegisteredAgent, shipped: dict[str, Any] | None) -> str:
    """up_to_date | outdated | updating | failed | too_old | unknown."""
    if _running(db, agent.id) is not None:
        return "updating"
    if shipped is None or agent.version_seen_at is None:
        return "unknown"
    if agent.build_id == shipped["build_id"]:
        return "up_to_date"
    if not agent.build_id:
        return "too_old"  # announces no build: older than the self-update
    last = _last(db, agent.id)
    if last is not None and last.to_build == shipped["build_id"] and last.status in {"failed", "rolled_back", "too_old"}:
        return "too_old" if last.status == "too_old" else "failed"
    return "outdated"


def _last(db: Session, agent_id: int) -> AgentUpdate | None:
    return db.scalar(select(AgentUpdate).where(AgentUpdate.agent_id == agent_id).order_by(AgentUpdate.id.desc()).limit(1))


def overview(db: Session) -> dict[str, Any]:
    shipped = shipped_agent()
    agents = []
    for agent in active_agents(db):
        agents.append(
            {
                "id": agent.id,
                "role": agent.role,
                "hostname": agent.hostname,
                "version": agent.agent_version,
                "build_id": agent.build_id,
                "version_seen_at": _aware(agent.version_seen_at),
                "state": agent_state(db, agent, shipped),
            }
        )
    history = list(db.scalars(select(AgentUpdate).order_by(AgentUpdate.id.desc()).limit(HISTORY_LIMIT)))
    return {
        "auto_update": bool(get_settings().auto_update_agents),
        "shipped": None if shipped is None else {"version": shipped["version"], "build_id": shipped["build_id"]},
        "agents": agents,
        "history": [_row(item) for item in history],
    }


def _row(item: AgentUpdate) -> dict[str, Any]:
    return {
        "id": item.id,
        "agent_id": item.agent_id,
        "role": item.role,
        "trigger": item.trigger,
        "status": item.status,
        "code": item.code,
        "message": item.message,
        "from_build": item.from_build,
        "to_build": item.to_build,
        "from_version": item.from_version,
        "to_version": item.to_version,
        "started_at": _aware(item.started_at),
        "finished_at": _aware(item.finished_at),
    }


def _finish(db: Session, row: AgentUpdate, status: str, code: str | None, message: str | None) -> AgentUpdate:
    row.status = status
    row.code = code
    row.message = message
    row.finished_at = _utcnow()
    db.add(row)
    db.commit()
    return row


# --- starting an update --------------------------------------------------------------------------


def start_update(
    db: Session,
    agent: RegisteredAgent,
    *,
    trigger: str,
    wait: bool = False,
    executor: Callable[[int], None] | None = None,
) -> AgentUpdate:
    """Creates the history row then runs the update (thread, or inline when
    `wait`). Refusals are AgentUpdateError (code for the UI)."""
    if trigger not in TRIGGERS:
        raise ValueError(trigger)
    shipped = shipped_agent()
    if shipped is None:
        raise AgentUpdateError("no_package", "This app has no agent sources to serve.")
    if _running(db, agent.id) is not None:
        raise AgentUpdateError("update_running", "An update of this agent is already running.")
    row = AgentUpdate(
        agent_id=agent.id,
        role=agent.role,
        trigger=trigger,
        status="running",
        from_build=agent.build_id,
        to_build=str(shipped["build_id"]),
        from_version=agent.agent_version,
        to_version=str(shipped["version"]),
    )
    db.add(row)
    db.commit()
    if run_active(db):
        _finish(db, row, "busy", "run_active", "A backup, verification or repair is running: the agent will be updated after it.")
        raise AgentUpdateError("run_active", "A run is in progress: try again when it is over.")
    run = executor or execute_update
    if wait:
        run(row.id)
    else:
        _spawn(run, row.id)
    db.refresh(row)
    return row


def _spawn(run: Callable[[int], None], update_id: int) -> None:
    """Background run (replaced by a recorder in the test suite)."""
    threading.Thread(target=run, args=(update_id,), name=f"agent-update-{update_id}", daemon=True).start()


def on_heartbeat(db: Session, agent: RegisteredAgent | None, version: str | None, build_id: str | None) -> AgentUpdate | None:
    """Every heartbeat: record what the agent runs, start an automatic update
    when it is older than the shipped build (once per build)."""
    if agent is None:
        return None
    record_announced(db, agent, version, build_id)
    return maybe_auto_update(db, agent)


def maybe_auto_update(db: Session, agent: RegisteredAgent, *, trigger: str = "auto", wait: bool = False) -> AgentUpdate | None:
    if not get_settings().auto_update_agents:
        return None
    shipped = shipped_agent()
    if shipped is None or agent.build_id == shipped["build_id"]:
        return None
    last = _last(db, agent.id)
    if last is not None and last.to_build == shipped["build_id"] and last.status in {"failed", "rolled_back", "too_old"}:
        return None  # not retried automatically: "Update now" does
    if not agent.build_id and last is not None and last.status == "too_old":
        return None
    try:
        return start_update(db, agent, trigger=trigger, wait=wait)
    except AgentUpdateError as exc:
        logger.info("Automatic agent update not started: %s", exc)
        return None


# --- running it ------------------------------------------------------------------------------------


def _client(agent: RegisteredAgent, timeout: float = 30.0) -> Any:
    from app.services.host_agent import HostAgentClient

    return HostAgentClient(
        base_url=agent.base_url,
        token=decrypt_value(agent.token_encrypted),
        timeout_seconds=timeout,
        label=f"{agent.role.capitalize()} agent",
        fingerprint=agent.fingerprint,
    )


def execute_update(
    update_id: int,
    *,
    session_factory: Callable[[], Session] | None = None,
    client_factory: Callable[[RegisteredAgent], Any] = _client,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    timeout: float = APPLY_TIMEOUT_SECONDS,
) -> None:
    if session_factory is None:
        from app.db.session import SessionLocal

        session_factory = SessionLocal
    with session_factory() as db:
        row = db.get(AgentUpdate, update_id)
        agent = db.get(RegisteredAgent, row.agent_id) if row and row.agent_id else None
        if row is None or agent is None:
            return
        try:
            status, code, message = _drive(db, row, agent, client_factory(agent), sleep=sleep, clock=clock, timeout=timeout)
        except SecretDecryptionError:
            status, code, message = "failed", "agent_token_unusable", "The agent token cannot be decrypted."
        except Exception as exc:  # noqa: BLE001 — recorded, never raised in the thread
            logger.exception("Agent update %s failed", update_id)
            status, code, message = "failed", "update_error", str(exc)
        _finish(db, row, status, code, message)
        _report(db, row, agent)


def _drive(db: Session, row: AgentUpdate, agent: RegisteredAgent, client: Any, *, sleep, clock, timeout) -> tuple[str, str | None, str]:
    from app.services.host_agent import HostAgentError

    try:
        version = client.get_version()
    except HostAgentError as exc:
        return "failed", "agent_unreachable", f"The agent does not answer: {exc}"
    record_announced(db, agent, version.get("agent_version"), version.get("build_id"))
    if version.get("build_id") == row.to_build:
        return "up_to_date", None, "The agent already runs this build."
    if "self-update" not in set(version.get("capabilities") or []):
        return "too_old", "agent_too_old", "This agent is too old to update itself: run its install command once more."
    shipped = shipped_agent() or {}
    try:
        started = client.post("/self-update", {"build_id": row.to_build, "sha256": shipped.get("sha256")}).payload
    except HostAgentError as exc:
        code = str((exc.payload or {}).get("error_code") or "self_update_refused")
        return "failed", code, str((exc.payload or {}).get("message") or exc)
    if started.get("status") == "up_to_date":
        return "up_to_date", None, "The agent already runs this build."
    deadline = clock() + timeout
    state: dict[str, Any] = {}
    while clock() < deadline:
        sleep(POLL_SECONDS)
        try:
            answer = client.get("/self-update/status").payload
        except HostAgentError:
            continue  # restarting
        state = answer.get("update") or {}
        if state.get("to_build") != row.to_build:
            continue
        if state.get("status") == "success" and answer.get("build_id") == row.to_build:
            record_announced(db, agent, row.to_version, row.to_build)
            return "success", None, str(state.get("message") or "Updated.")
        if state.get("status") in {"rolled_back", "failed"}:
            code = "health_check_failed" if state["status"] == "rolled_back" else "update_failed"
            return state["status"], code, str(state.get("message") or "")
    return "failed", "agent_unreachable_after_update", "The agent did not answer after the update."


def _report(db: Session, row: AgentUpdate, agent: RegisteredAgent) -> None:
    """Activity entry + notification (alert on failure / rollback)."""
    if row.status in {"up_to_date"}:
        return
    level = {"success": "success", "too_old": "warning", "busy": "info"}.get(row.status, "error")
    db.add(
        ActivityEvent(
            created_at=datetime.utcnow(),
            category="agent",
            action=f"agent_update_{row.status}",
            status=level,
            summary=f"{agent.role} agent {agent.hostname}: update to {row.to_version} ({row.to_build}) — {row.status}. {row.message or ''}".strip(),
            details={"agent_id": agent.id, "update_id": row.id, "code": row.code, "to_build": row.to_build, "from_build": row.from_build},
        )
    )
    db.commit()
    if row.status in {"rolled_back", "failed", "too_old"}:
        from app.services import notifications

        notifications.agent_update_failed(agent.role, agent.hostname, row.message or row.status)


def update_all(db: Session, *, trigger: str, wait: bool = True) -> list[AgentUpdate]:
    """End of the LXC `update`: every active agent, when the option is on."""
    rows = []
    for agent in active_agents(db):
        row = maybe_auto_update(db, agent, trigger=trigger, wait=wait)
        if row is not None:
            rows.append(row)
    return rows
