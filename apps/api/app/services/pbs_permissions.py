"""Privileges the PBO token needs on PBS, and the check of its effective ones.

Required per path (PBS 4.x source, `access:` blocks + in-handler checks):

  /datastore     Datastore.Allocate   POST /config/datastore (re-create a deleted
                                      config, reuse-datastore) — checked on /datastore
                 Datastore.Modify     PBO marker comment, mount/unmount, prune, gc
                 Datastore.Audit      datastore status, snapshot list (disk contents)
                 Datastore.Read       local sync: read the source datastore
                 Datastore.Backup     local sync job into the target datastore
                 Datastore.Prune      prune-datastore (retention)
                 Datastore.Verify     verify of the copied snapshots
  /system/disks  Sys.Audit            disks/list (visibility, partitions, UUIDs)
                 Sys.Modify           wipedisk, disks/directory (first creation),
                                      mount/unmount, POST /config/datastore with a
                                      backing-device
  /system/tasks  Sys.Audit            follow tasks the token did not start

No built-in role but `Admin` holds Datastore.Allocate (DatastoreAdmin =
Audit | Modify | Read | Verify | Backup | Prune), hence `Admin` on /datastore.
The first creation goes through disks/directory (Sys.Modify only), which is why
a token with DatastoreAdmin could create a datastore but not re-create it.

`GET /access/permissions?path=…` lets a token read its own privileges
(path → privilege → propagate). Unknown answer (old PBS, error) → no verdict.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from app.services.pbs_provisioning import PBS_ACLS

REQUIRED_PRIVILEGES: dict[str, tuple[str, ...]] = {
    "/datastore": (
        "Datastore.Allocate",
        "Datastore.Modify",
        "Datastore.Audit",
        "Datastore.Read",
        "Datastore.Backup",
        "Datastore.Prune",
        "Datastore.Verify",
    ),
    "/system/disks": ("Sys.Audit", "Sys.Modify"),
    "/system/tasks": ("Sys.Audit",),
}
CACHE_SECONDS = 60.0

_lock = threading.Lock()
_cache: dict[tuple[str, str], tuple[float, dict[str, list[str]] | None]] = {}


def reset_cache() -> None:
    with _lock:
        _cache.clear()


def missing_privileges(client: Any) -> dict[str, list[str]] | None:
    """{path: [missing privileges]} for the token behind `client`; None when
    PBS cannot tell (the check is then skipped)."""
    missing: dict[str, list[str]] = {}
    try:
        for path, needed in REQUIRED_PRIVILEGES.items():
            granted = client.permissions(path)
            held = set((granted or {}).get(path) or {})
            lacking = [privilege for privilege in needed if privilege not in held]
            if lacking:
                missing[path] = lacking
    except Exception:  # noqa: BLE001 - unreachable PBS or endpoint unavailable: no verdict
        return None
    return missing


def cached_missing(settings: Any, client: Any) -> dict[str, list[str]] | None:
    key = (settings.pbs_api_url, settings.pbs_token_id)
    now = time.monotonic()
    with _lock:
        cached = _cache.get(key)
        if cached and now - cached[0] < CACHE_SECONDS:
            return cached[1]
    value = missing_privileges(client)
    with _lock:
        _cache[key] = (now, value)
    return value


def fix_command(token_id: str, missing: dict[str, list[str]]) -> str:
    """Exact commands to run on PBS (user AND token: a token's privileges are
    the intersection of its own ACLs and its user's)."""
    roles = dict(PBS_ACLS)
    user = token_id.split("!", 1)[0] if "!" in token_id else token_id
    commands = []
    for path in missing:
        role = roles.get(path, "Admin")
        commands.append(f"proxmox-backup-manager acl update {path} {role} --auth-id {user}")
        if token_id != user:
            commands.append(f"proxmox-backup-manager acl update {path} {role} --auth-id '{token_id}'")
    return " && ".join(commands)


def reason(missing: dict[str, list[str]] | None, token_id: str) -> dict[str, Any] | None:
    """Readiness reason (blocking) for missing privileges."""
    if not missing:
        return None
    flat = [f"{privilege} ({path})" for path, privileges in missing.items() for privilege in privileges]
    command = fix_command(token_id, missing)
    cannot_create = "Datastore.Allocate" in missing.get("/datastore", [])
    return {
        "code": "pbs_token_cannot_create_datastore" if cannot_create else "pbs_token_missing_privileges",
        "message": (
            ("The PBO token cannot create a datastore on PBS" if cannot_create else "The PBO token lacks PBS privileges")
            + f" (missing: {', '.join(flat)}). On PBS, run: {command}"
        ),
        "privileges": ", ".join(flat),
        "command": command,
    }


def check(settings: Any, client: Any) -> dict[str, Any] | None:
    return reason(cached_missing(settings, client), settings.pbs_token_id)


def denied_message(client: Any, token_id: str, operation: str) -> tuple[str, str]:
    """(error_code, readable message) for a 403 seen during a run, instead of
    the raw HTTP error."""
    found = reason(missing_privileges(client), token_id)
    if found is None:
        command = fix_command(token_id, dict.fromkeys(REQUIRED_PRIVILEGES, []))
        return (
            "pbs_permission_denied",
            f"PBS refused {operation}: the PBO token lacks a privilege. On PBS, run: {command}",
        )
    return found["code"], found["message"]
