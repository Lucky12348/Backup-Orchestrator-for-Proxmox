"""Run a Proxmox backup job now (like PVE's "Run now"), Phase 5h.1.

PVE has no "run job" endpoint: its web UI's "Run now" posts the job's own
parameters to `POST /nodes/{node}/vzdump`. PBO does the same, on the job's
node (or the configured PVE node), and follows the task by its UPID
(`GET /nodes/{node}/tasks/{upid}/status`).

Privileges (docs/PERMISSIONS.md): `VM.Backup` on the guests and
`Datastore.AllocateSpace` on the job's storage. A token provisioned before
this feature lacks them: the 403 is turned into `pve_permission_missing`
with the exact `pveum role modify` command (keeps the token).
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

from app.services.proxmox_client import (
    BACKUP_JOB_READ_ONLY_FIELDS,
    ProxmoxClient,
    flatten_pve_property_value,
)
from app.services.setup_wizard import PVE_PRIVILEGES, PVE_ROLE

# Job settings that are not vzdump parameters (schedule, state, metadata).
JOB_ONLY_FIELDS = BACKUP_JOB_READ_ONLY_FIELDS | {"schedule", "enabled", "comment", "repeat-missed", "starttime", "dow", "node"}
RUN_PRIVILEGES = ("VM.Backup", "Datastore.AllocateSpace")
_NODE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]{0,62}$")
_UPID_RE = re.compile(r"^UPID:(?P<node>[A-Za-z0-9][A-Za-z0-9.-]{0,62}):[0-9A-Fa-f:]+:[a-z0-9_-]+:[^/\s]*:[^/\s]*:?$")


class JobRunError(RuntimeError):
    def __init__(self, code: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.code = code
        self.extra = extra


def role_command() -> str:
    """Grants the missing privileges without re-creating the token."""
    return f'pveum role modify {PVE_ROLE} --privs "{",".join(PVE_PRIVILEGES)}"'


def vzdump_params(job: dict[str, Any]) -> dict[str, Any]:
    """The job's backup parameters, flattened for the vzdump call."""
    params = {key: flatten_pve_property_value(value) for key, value in job.items() if key not in JOB_ONLY_FIELDS and value is not None}
    if not (params.get("vmid") or params.get("pool") or str(params.get("all") or "0") in {"1", "true"}):
        raise JobRunError("job_nothing_selected", "This Proxmox job selects no VM or container.")
    return params


def target_node(job: dict[str, Any], default_node: str) -> str:
    node = str(job.get("node") or default_node or "").strip()
    if not _NODE_RE.match(node):
        raise JobRunError("job_node_unknown", "The Proxmox node to run this job on is unknown.")
    return node


def _translate(exc: RuntimeError) -> JobRunError:
    text = str(exc)
    if "HTTP 403" in text:
        return JobRunError(
            "pve_permission_missing",
            f"The PBO token may not start backups (needs {', '.join(RUN_PRIVILEGES)}). On the Proxmox host, run: {role_command()}",
            command=role_command(),
            privileges=list(RUN_PRIVILEGES),
        )
    return JobRunError("job_run_failed", text)


def run_job_now(client: ProxmoxClient, job_id: str, default_node: str) -> dict[str, Any]:
    try:
        job = client.get_backup_job(job_id)
    except RuntimeError as exc:
        raise _translate(exc) from None
    if not job:
        raise JobRunError("job_not_found", "This Proxmox job no longer exists.")
    node = target_node(job, default_node)
    params = vzdump_params(job)
    try:
        upid = client._post(f"nodes/{quote(node, safe='')}/vzdump", data=params)
    except RuntimeError as exc:
        raise _translate(exc) from None
    if not isinstance(upid, str) or not _UPID_RE.match(upid):
        raise JobRunError("job_run_failed", f"Proxmox did not return a task id: {upid!r}")
    return {"job_id": job_id, "node": node, "upid": upid}


def task_status(client: ProxmoxClient, node: str, upid: str) -> dict[str, Any]:
    """{"running": bool, "ok": bool | None, "exitstatus": str | None} of a vzdump task."""
    match = _UPID_RE.match(upid or "")
    if not _NODE_RE.match(node or "") or not match or match.group("node") != node:
        raise JobRunError("task_unknown", "Unknown Proxmox task.")
    try:
        payload = client._get(f"nodes/{quote(node, safe='')}/tasks/{quote(upid, safe='')}/status")
    except RuntimeError as exc:
        raise _translate(exc) from None
    payload = payload if isinstance(payload, dict) else {}
    running = payload.get("status") != "stopped"
    exitstatus = None if running else str(payload.get("exitstatus") or "")
    return {"node": node, "upid": upid, "running": running, "ok": None if running else exitstatus == "OK", "exitstatus": exitstatus}
