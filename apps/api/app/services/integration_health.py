"""Integrations page (Phase 6b): Proxmox VE, Backup Server and the host agent
as three cards — state, version, latency, last sync, pinned TLS fingerprint.

`check(key)` really calls the system (the "Test" button); the page asks for
all three on load. Failures are coded (`unreachable`, `not_configured`) with
the technical message for the details; secrets never leave the server.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import datetime
from typing import Any

from app.core.config import get_settings

KEYS = ("pve", "pbs", "agent")


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat() + ("Z" if value.tzinfo is None else "")


def _timed(call: Callable[[], Any]) -> tuple[Any, int]:
    started = time.perf_counter()
    result = call()
    return result, int((time.perf_counter() - started) * 1000)


def _pve(settings: Any) -> dict[str, Any]:
    from app.services.proxmox_client import ProxmoxClient
    from app.services.sync_state import get_proxmox_sync_state

    state = get_proxmox_sync_state()
    base = {
        "key": "pve",
        "configured": bool(settings.pve_api_url and settings.pve_api_token_id),
        "url": settings.pve_api_url or None,
        "fingerprint": settings.pve_fingerprint or None,
        "node": settings.pve_node_name or None,
        "last_sync_at": _iso(state.last_sync_at),
        "last_sync_error": state.last_error,
    }
    if not base["configured"]:
        return {**base, "ok": False, "code": "not_configured", "version": None, "latency_ms": None, "message": None}
    try:
        payload, latency = _timed(lambda: ProxmoxClient(settings)._get("version"))
    except Exception as exc:  # noqa: BLE001 - shown as "unreachable"
        return {**base, "ok": False, "code": "unreachable", "version": None, "latency_ms": None, "message": str(exc)}
    version = payload.get("version") if isinstance(payload, dict) else None
    return {**base, "ok": True, "code": None, "version": version, "latency_ms": latency, "message": None}


def _pbs(settings: Any) -> dict[str, Any]:
    from app.services.pbs_client import PBSClient
    from app.services.sync_state import get_pbs_sync_state

    state = get_pbs_sync_state()
    base = {
        "key": "pbs",
        "configured": bool(settings.pbs_api_url and settings.pbs_token_id),
        "url": settings.pbs_api_url or None,
        "fingerprint": settings.pbs_fingerprint or None,
        "node": settings.pbs_datastore or None,
        "last_sync_at": _iso(state.last_sync_at),
        "last_sync_error": state.last_error,
    }
    if not base["configured"]:
        return {**base, "ok": False, "code": "not_configured", "version": None, "latency_ms": None, "message": None}
    try:
        payload, latency = _timed(lambda: PBSClient(settings).get_version())
    except Exception as exc:  # noqa: BLE001
        return {**base, "ok": False, "code": "unreachable", "version": None, "latency_ms": None, "message": str(exc)}
    version = payload.get("version") if isinstance(payload, dict) else None
    return {**base, "ok": True, "code": None, "version": version, "latency_ms": latency, "message": None}


def _agent(settings: Any) -> dict[str, Any]:
    from app.services.host_agent import get_host_agent_client

    base = {
        "key": "agent",
        "configured": bool(settings.host_agent_base_url),
        "url": settings.host_agent_base_url or None,
        "fingerprint": getattr(settings, "host_agent_fingerprint", "") or None,
        "node": None,
        "last_sync_at": None,
        "last_sync_error": None,
    }
    if not base["configured"]:
        return {**base, "ok": False, "code": "not_configured", "version": None, "latency_ms": None, "message": None}
    try:
        payload, latency = _timed(lambda: get_host_agent_client().get_version())
    except Exception as exc:  # noqa: BLE001
        return {**base, "ok": False, "code": "unreachable", "version": None, "latency_ms": None, "message": str(exc)}
    version = payload.get("version") or payload.get("agent_version") if isinstance(payload, dict) else None
    hostname = payload.get("hostname") if isinstance(payload, dict) else None
    return {**base, "ok": True, "code": None, "version": version, "latency_ms": latency, "node": hostname, "message": None}


CHECKS: dict[str, Callable[[Any], dict[str, Any]]] = {"pve": _pve, "pbs": _pbs, "agent": _agent}


def check(key: str, settings: Any | None = None) -> dict[str, Any]:
    return CHECKS[key](settings or get_settings())


def check_all() -> list[dict[str, Any]]:
    """The three checks in parallel: the page waits for the slowest, not the sum."""
    from concurrent.futures import ThreadPoolExecutor

    settings = get_settings()
    with ThreadPoolExecutor(max_workers=len(KEYS)) as pool:
        return list(pool.map(lambda key: check(key, settings), KEYS))
