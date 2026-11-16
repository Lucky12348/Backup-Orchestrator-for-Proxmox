"""Can an external backup start on this disk right now, and if not, why?

`GET /api/v1/external-backups/readiness/{disk_id}` replaces the old front-end
guess (which looked for a PBS *agent* even in `PBS_MODE=api`). Each reason has
a stable `code` (translated by the UI) and an English `message`. The checks
mirror what `run_external_backup` / `assert_external_backup_capabilities`
enforce, so a "ready" button does not fail at the first step.

Agent version probes are cached briefly: the Disks page asks for every disk.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.backup_run import BackupRunStatus
from app.models.external_backup_run import ExternalBackupRun
from app.models.external_disk import ExternalDisk
from app.services.disk_events import MIN_DISK_SIZE_GB, is_ejected, reject_reason
from app.services.external_backup_agent import (
    REQUIRED_HOST_CAPABILITIES,
    REQUIRED_PBS_CAPABILITIES,
)
from app.services.host_agent import HostAgentClient
from app.services.settings_service import effective_settings, stored_secret

VERSION_CACHE_SECONDS = 30.0

_cache_lock = threading.Lock()
_version_cache: dict[tuple[str, str], tuple[float, dict[str, Any] | None, str | None]] = {}


def reset_version_cache() -> None:
    with _cache_lock:
        _version_cache.clear()


def _cached_version(key: tuple[str, str], fetch: Callable[[], dict[str, Any]]) -> tuple[dict[str, Any] | None, str | None]:
    now = time.monotonic()
    with _cache_lock:
        cached = _version_cache.get(key)
        if cached and now - cached[0] < VERSION_CACHE_SECONDS:
            return cached[1], cached[2]
    try:
        payload, error = fetch(), None
    except Exception as exc:  # noqa: BLE001 — any failure means "not reachable"
        payload, error = None, str(exc)
    with _cache_lock:
        _version_cache[key] = (now, payload, error)
    return payload, error


def _missing(payload: dict[str, Any] | None, required: set[str]) -> list[str]:
    capabilities = payload.get("capabilities") if isinstance(payload, dict) else None
    if not isinstance(capabilities, list):
        return sorted(required)
    return sorted(required - {str(item) for item in capabilities})


def _reason(code: str, message: str, **params: Any) -> dict[str, Any]:
    return {"code": code, "message": message, **params}


_REJECT_MESSAGES = {
    "zero_size": "The disk reports a size of 0 (cable, port or enclosure problem).",
    "too_small": f"The disk is too small for a PBS datastore (minimum {MIN_DISK_SIZE_GB} GiB).",
    "operator": "The disk was rejected by the operator.",
}


def local_disk_reasons(db: Session, disk: ExternalDisk) -> list[dict[str, Any]]:
    """The checks that need no network call: the disk itself and the other
    runs. `run_external_backup` refuses with them too (409, no run created)."""
    reasons: list[dict[str, Any]] = []
    rejected = reject_reason(disk)
    if rejected:
        reasons.append(_reason(f"disk_{rejected}", _REJECT_MESSAGES[rejected], min_size_gb=MIN_DISK_SIZE_GB))
    if is_ejected(disk):
        # B25: safely ejected, still plugged in: unplug and plug it back first.
        reasons.append(_reason("disk_ejected", "The disk was ejected: unplug it, then plug it back in."))
    elif not disk.connected:
        reasons.append(_reason("disk_not_connected", "The disk is not plugged in."))
    if not disk.trusted:
        reasons.append(_reason("disk_not_approved", "The disk is not approved for backups yet."))
    active = db.scalar(
        select(ExternalBackupRun.id)
        .where(ExternalBackupRun.status.in_([BackupRunStatus.PENDING, BackupRunStatus.RUNNING]))
        .limit(1)
    )
    if active is not None:
        reasons.append(_reason("run_active", "An external backup is already running.", run_id=active))
    return reasons


def external_backup_readiness(
    db: Session,
    disk: ExternalDisk,
    *,
    host_client: HostAgentClient | None = None,
    pbs_client: Any | None = None,
) -> dict[str, Any]:
    settings = effective_settings(db)
    mode = settings.pbs_mode
    reasons: list[dict[str, Any]] = []

    reasons.extend(local_disk_reasons(db, disk))

    # --- Proxmox VE + host agent (USB passthrough) -----------------------------------------
    pve_secret = stored_secret(db, "PVE_API_TOKEN_SECRET") or settings.pve_api_token_secret
    if not (settings.pve_api_url and settings.pve_api_token_id and pve_secret):
        reasons.append(_reason("pve_not_configured", "Proxmox VE is not connected."))
    if not settings.pbs_execution_vm_id:
        reasons.append(_reason("pbs_vm_not_set", "The PBS VM (USB passthrough target) is not set."))
    if not settings.host_agent_base_url:
        reasons.append(_reason("host_agent_missing", "No host agent is installed."))
    else:
        client = host_client or HostAgentClient(
            base_url=settings.host_agent_base_url,
            token=settings.host_agent_token,
            timeout_seconds=min(settings.host_agent_timeout_seconds, 10.0),
            label="Host agent",
            fingerprint=settings.host_agent_fingerprint,
        )
        payload, error = _cached_version(("host", settings.host_agent_base_url), client.get_version)
        if error is not None:
            reasons.append(_reason("host_agent_unreachable", f"The host agent does not answer: {error}"))
        elif missing := _missing(payload, set(REQUIRED_HOST_CAPABILITIES)):
            reasons.append(
                _reason("host_agent_outdated", f"The host agent is too old (missing: {', '.join(missing)}).", missing=missing)
            )

    # --- PBS side: REST API or legacy PBS agent ------------------------------------------
    if mode == "api":
        secret = stored_secret(db, "PBS_TOKEN_SECRET") or settings.pbs_token_secret
        if not (settings.pbs_api_url and settings.pbs_token_id and secret):
            reasons.append(_reason("pbs_not_configured", "PBS is not connected."))
        elif not settings.pbs_datastore:
            reasons.append(_reason("pbs_datastore_missing", "No source PBS datastore is selected."))
        else:
            from app.services.pbs_api_backend import PBSApiBackend

            backend = pbs_client or PBSApiBackend(settings)
            payload, error = _cached_version(("pbs-api", settings.pbs_api_url), backend.get_version)
            if error is not None:
                reasons.append(_reason("pbs_unreachable", f"The PBS API does not answer: {error}"))
    else:
        if not settings.pbs_agent_base_url:
            reasons.append(_reason("pbs_agent_missing", "PBS_MODE=agent but no PBS agent is configured."))
        else:
            client = pbs_client or HostAgentClient(
                base_url=settings.pbs_agent_base_url,
                token=settings.pbs_agent_token,
                timeout_seconds=min(settings.pbs_agent_timeout_seconds, 10.0),
                label="PBS agent",
                fingerprint=settings.pbs_agent_fingerprint,
            )
            payload, error = _cached_version(("pbs-agent", settings.pbs_agent_base_url), client.get_version)
            if error is not None:
                reasons.append(_reason("pbs_agent_unreachable", f"The PBS agent does not answer: {error}"))
            elif missing := _missing(payload, set(REQUIRED_PBS_CAPABILITIES)):
                reasons.append(
                    _reason("pbs_agent_outdated", f"The PBS agent is too old (missing: {', '.join(missing)}).", missing=missing)
                )

    return {"disk_id": disk.id, "ready": not reasons, "pbs_mode": mode, "reasons": reasons}
