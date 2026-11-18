"""Settings › About (Phase 5j): PBO's version, the agents', and whether a
newer release exists.

The installed version is the `VERSION` file the installer / `update` writes at
the root of the install ("v0.3.0", "main@1a2b3c4", "local-…"). The latest
release is asked to GitHub (same source as `update`), at most every 6 hours;
any failure just says "unknown" — the page never depends on the Internet.
"""

from __future__ import annotations

import re
import threading
import time
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import RegisteredAgent

REPOSITORY = "Lucky12348/Backup-Orchestrator-for-Proxmox"
LINKS = {
    "repository": f"https://github.com/{REPOSITORY}",
    "releases": f"https://github.com/{REPOSITORY}/releases",
    "docs": f"https://github.com/{REPOSITORY}/tree/main/docs",
}
CACHE_SECONDS = 6 * 3600
_cache: dict[str, Any] = {"at": 0.0, "value": None}
_lock = threading.Lock()


def installed_version() -> str:
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "VERSION"
        if candidate.is_file():
            text = candidate.read_text(encoding="utf-8").strip()
            if text:
                return text
    return "dev"


def _numbers(version: str) -> tuple[int, ...] | None:
    match = re.match(r"^v?(\d+)\.(\d+)\.(\d+)", version or "")
    return tuple(int(part) for part in match.groups()) if match else None


def update_available(installed: str, latest: str | None) -> bool | None:
    """True / False when both are release versions, None otherwise (a branch install)."""
    mine, theirs = _numbers(installed), _numbers(latest or "")
    if mine is None or theirs is None:
        return None
    return theirs > mine


def latest_release(*, refresh: bool = False, fetch: Any = None) -> dict[str, Any] | None:
    with _lock:
        if not refresh and _cache["value"] is not None and time.monotonic() - _cache["at"] < CACHE_SECONDS:
            return _cache["value"]
    try:
        if fetch is not None:
            data = fetch()
        else:
            response = httpx.get(f"https://api.github.com/repos/{REPOSITORY}/releases/latest", timeout=5, headers={"Accept": "application/vnd.github+json"})
            response.raise_for_status()
            data = response.json()
        value = {"version": str(data.get("tag_name") or ""), "url": str(data.get("html_url") or LINKS["releases"]), "published_at": data.get("published_at")}
    except Exception:  # noqa: BLE001 — offline, rate-limited… the page stays usable
        value = None
    with _lock:
        _cache.update(at=time.monotonic(), value=value)
    return value


def about(db: Session, *, refresh: bool = False) -> dict[str, Any]:
    from app.services.agent_bootstrap import shipped_agent

    version = installed_version()
    latest = latest_release(refresh=refresh)
    try:
        package = shipped_agent()
        shipped = {"version": str(package["version"]), "build_id": str(package["build_id"])} if package else None
    except Exception:  # noqa: BLE001
        shipped = None
    agents = [
        {
            "role": agent.role,
            "hostname": agent.hostname,
            "version": agent.agent_version,
            "build_id": agent.build_id,
            "up_to_date": bool(shipped and agent.build_id == shipped["build_id"]),
            "last_seen_at": agent.last_seen_at,
        }
        for agent in db.scalars(select(RegisteredAgent).where(RegisteredAgent.revoked_at.is_(None)).order_by(RegisteredAgent.role))
    ]
    return {
        "version": version,
        "latest": latest,
        "update_available": update_available(version, latest["version"] if latest else None),
        "agent_build": shipped,
        "agents": agents,
        "links": LINKS,
        "update_command": "update",
    }
