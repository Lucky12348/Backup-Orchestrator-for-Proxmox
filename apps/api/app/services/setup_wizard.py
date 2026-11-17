"""First-run wizard: progress (setup_progress) and automatic provisioning.

Steps: admin → agent → pve → pbs → done. The admin step is implicit (done
once an account exists); the others are stored server-side so a reload
resumes where the operator left off. Only non-secret results are kept in
`setup_progress.data`; tokens go to `app_settings` (encrypted).
"""

from __future__ import annotations

import json
import logging
import socket
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.models.setup_progress import SetupProgress
from app.services.agent_registry import active_agent
from app.services.host_agent import HostAgentClient, HostAgentError
from app.services.settings_service import (
    effective_settings,
    stored_secret,
    update_settings,
)
from app.services.setup_probe import (
    ProbeError,
    normalize_api_url,
    normalize_fingerprint,
    probe_proxmox,
)
from app.services.users import has_users

logger = logging.getLogger(__name__)

# "notifications" is prepared for an optional step (hidden in the UI for now).
STEPS = ("admin", "agent", "pve", "pbs", "notifications", "done")
PROGRESS_ID = 1
MAX_PROGRESS_BYTES = 32_000
_SECRET_MARKERS = ("secret", "password", "passwd", "ticket")

# Names and privileges shown in the "manual configuration" commands. The PVE
# list must match apps/agent/src/agent/pve_access.py (checked by a test).
PVE_USER = "pbo@pve"
PVE_TOKEN_NAME = "pbo"
PVE_ROLE = "PBO"
PVE_PRIVILEGES = ("Sys.Audit", "Sys.Modify", "VM.Audit", "Datastore.Allocate")


class SetupError(RuntimeError):
    """`code` is stable for the UI; the message tells the operator what to do."""

    def __init__(self, code: str, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --- Progress -----------------------------------------------------------------


def _row(db: Session) -> SetupProgress | None:
    return db.get(SetupProgress, PROGRESS_ID)


def _load_data(row: SetupProgress | None) -> dict[str, Any]:
    if row is None:
        return {}
    try:
        data = json.loads(row.data or "{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def sanitize_progress_data(data: Any) -> Any:
    """Drop any key that looks like a secret, at every depth."""
    if isinstance(data, dict):
        return {
            str(key): sanitize_progress_data(value)
            for key, value in data.items()
            if not any(marker in str(key).lower() for marker in _SECRET_MARKERS)
        }
    if isinstance(data, list):
        return [sanitize_progress_data(item) for item in data]
    if isinstance(data, (str, int, float, bool)) or data is None:
        return data
    return str(data)


def _legacy_configured(db: Session) -> bool:
    settings = effective_settings(db)
    return bool(settings.pve_api_url and settings.pve_api_token_id and settings.pve_api_token_secret)


def setup_completed(db: Session) -> bool:
    """Done when the wizard was finished, or for an install configured before
    the wizard existed (upgrade: admin + Proxmox connection already there)."""
    row = _row(db)
    if row is not None:
        return row.completed_at is not None
    return has_users(db) and _legacy_configured(db)


def current_step(db: Session) -> str:
    if not has_users(db):
        return "admin"
    if setup_completed(db):
        return "done"
    row = _row(db)
    step = row.current_step if row is not None else "agent"
    return step if step in STEPS[1:] else "agent"


def get_progress(db: Session) -> dict[str, Any]:
    row = _row(db)
    return {
        "current_step": current_step(db),
        "completed": setup_completed(db),
        "data": _load_data(row),
        "updated_at": row.updated_at if row is not None else None,
    }


def save_progress(db: Session, *, step: str | None = None, data: dict[str, Any] | None = None, merge: bool = True) -> dict[str, Any]:
    if step is not None and step not in STEPS[1:]:
        raise SetupError("invalid_step", f"Unknown setup step `{step}`.")
    row = _row(db)
    if row is None:
        row = SetupProgress(id=PROGRESS_ID, current_step="agent", data="{}")
        db.add(row)
    if step is not None:
        row.current_step = step
    if data is not None:
        clean = sanitize_progress_data(data)
        merged = {**_load_data(row), **clean} if merge else clean
        encoded = json.dumps(merged, separators=(",", ":"), default=str)
        if len(encoded) > MAX_PROGRESS_BYTES:
            raise SetupError("progress_too_large", "Setup state is too large.")
        row.data = encoded
    row.updated_at = _utcnow()
    db.commit()
    return get_progress(db)


def complete_setup(db: Session) -> dict[str, Any]:
    settings = effective_settings(db)
    if not (settings.pve_api_url and settings.pve_api_token_id and stored_secret(db, "PVE_API_TOKEN_SECRET")):
        raise SetupError("pve_missing", "Connect Proxmox VE first (step 3).")
    if not (settings.pbs_api_url and settings.pbs_token_id and settings.pbs_datastore):
        raise SetupError("pbs_missing", "Connect PBS and choose the source datastore first (step 4).")
    progress = save_progress(db, step="done")
    row = _row(db)
    if row is not None and row.completed_at is None:
        row.completed_at = _utcnow()
        db.commit()
    return {**progress, "current_step": "done", "completed": True}


def reset_setup_progress(db: Session) -> None:
    row = _row(db)
    if row is not None:
        db.delete(row)
        db.commit()


# --- Host agent ---------------------------------------------------------------


def agent_status(db: Session) -> dict[str, Any]:
    agent = active_agent(db, "host")
    if agent is None:
        return {"registered": False, "connected": False}
    return {
        "registered": True,
        "connected": agent.last_seen_at is not None,
        "agent_id": agent.id,
        "hostname": agent.hostname,
        "base_url": agent.base_url,
        "fingerprint": agent.fingerprint,
        "last_seen_at": agent.last_seen_at,
    }


def host_agent_client(db: Session) -> HostAgentClient:
    settings = effective_settings(db)
    if not settings.host_agent_fingerprint or not settings.host_agent_base_url:
        raise SetupError("agent_missing", "No host agent is registered yet: finish step 2 (agent installation) first.")
    return HostAgentClient(
        base_url=settings.host_agent_base_url,
        token=settings.host_agent_token,
        # pveum + certificate reading take a few seconds; guest agent queries up to ~6 s each.
        timeout_seconds=max(settings.host_agent_timeout_seconds, 60.0),
        label="Host agent",
        fingerprint=settings.host_agent_fingerprint,
    )


def _agent_error(exc: HostAgentError, action: str) -> SetupError:
    if exc.status_code == 404:
        return SetupError(
            "agent_outdated",
            f"The host agent does not support {action}: it predates this version. "
            "Re-run its install command (step 2) to update it, or use the manual configuration.",
            status_code=502,
        )
    if exc.status_code is None:
        return SetupError(
            "agent_unreachable",
            f"The host agent is unreachable ({exc}). Check that pbo-agent.service runs on the Proxmox host "
            "and that this container reaches its port 8090.",
            status_code=502,
        )
    return SetupError("agent_failed", f"The host agent could not complete {action}: {exc}", status_code=502)


# --- Step 3: Proxmox VE -------------------------------------------------------


def provision_pve(
    db: Session,
    *,
    agent_client: HostAgentClient | None = None,
    transport: httpx.BaseTransport | None = None,
    retries: int = 3,
    retry_delay: float = 1.0,
) -> dict[str, Any]:
    client = agent_client or host_agent_client(db)
    try:
        result = client.post("/pve/provision-access", {}).payload
    except HostAgentError as exc:
        raise _agent_error(exc, "the Proxmox VE access creation") from None

    try:
        token_id = str(result["token_id"])
        secret = str(result["secret"])
        node = str(result["node"])
        api_url = normalize_api_url(str(result["api_url"]), 8006)
        fingerprint = normalize_fingerprint(str(result["fingerprint"]))
    except (KeyError, ProbeError) as exc:
        raise SetupError("agent_failed", f"Unexpected answer from the host agent ({exc}).", status_code=502) from None
    if not fingerprint:
        raise SetupError("agent_failed", "The host agent returned no PVE certificate fingerprint.", status_code=502)

    probe = None
    last_error: ProbeError | None = None
    for attempt in range(max(retries, 1)):
        try:
            probe = probe_proxmox(
                api_url, token_id, secret, verify_ssl=False, fingerprint=fingerprint,
                transport=transport, include_containers=True,
            )
            break
        except ProbeError as exc:  # a brand-new token can take a moment to propagate
            last_error = exc
            if attempt + 1 < retries:
                time.sleep(retry_delay)
    if probe is None:
        raise SetupError(
            "pve_check_failed",
            f"The access was created but the API check failed: {last_error}. "
            "Check that this container reaches the Proxmox VE API (port 8006).",
            status_code=502,
        )

    update_settings(
        db,
        {
            "PVE_API_URL": api_url,
            "PVE_API_TOKEN_ID": token_id,
            "PVE_API_TOKEN_SECRET": secret,
            "PVE_VERIFY_SSL": False,
            "PVE_FINGERPRINT": fingerprint,
            "PVE_NODE_NAME": node,
        },
    )
    logger.info("Setup: Proxmox VE connected (%s, node %s, token %s)", api_url, node, token_id)
    summary = {
        "node": node,
        "pve_version": str(result.get("pve_version") or probe.version),
        "api_url": api_url,
        "fingerprint": fingerprint,
        "token_id": token_id,
        "vm_count": len(probe.vms),
        "ct_count": len(probe.containers),
        "vms": probe.vms,
    }
    save_progress(db, data={"pve": {k: v for k, v in summary.items() if k != "vms"}})
    return summary


def record_manual_pve(db: Session) -> dict[str, Any]:
    settings = effective_settings(db)
    summary = {
        "node": settings.pve_node_name,
        "api_url": settings.pve_api_url,
        "fingerprint": settings.pve_fingerprint,
        "token_id": settings.pve_api_token_id,
        "manual": True,
    }
    save_progress(db, data={"pve": summary})
    return summary


# --- Step 4: PBS --------------------------------------------------------------


def _port_open(address: str, port: int = 8007, timeout: float = 1.5) -> bool:
    try:
        with socket.create_connection((address, port), timeout=timeout):
            return True
    except OSError:
        return False


def pbs_candidates(
    db: Session,
    *,
    agent_client: HostAgentClient | None = None,
    transport: httpx.BaseTransport | None = None,
    port_probe: Callable[[str], bool] | None = None,
) -> dict[str, Any]:
    """VMs of the PVE inventory, with the likely PBS VM first: a guest agent
    address answering on 8007 weighs more than a name containing "pbs"."""
    settings = effective_settings(db)
    secret = stored_secret(db, "PVE_API_TOKEN_SECRET")
    if not (settings.pve_api_url and settings.pve_api_token_id and secret):
        raise SetupError("pve_missing", "Connect Proxmox VE first (step 3).")
    try:
        probe = probe_proxmox(
            settings.pve_api_url, settings.pve_api_token_id, secret,
            verify_ssl=settings.pve_verify_ssl, fingerprint=settings.pve_fingerprint, transport=transport,
        )
    except ProbeError as exc:
        raise SetupError("pve_check_failed", f"Cannot list the VMs: {exc}", status_code=502) from None

    running = [vm["vmid"] for vm in probe.vms if vm["status"] == "running"]
    addresses: dict[str, list[str]] = {}
    if running:
        try:
            client = agent_client or host_agent_client(db)
            addresses = client.post("/pve/guest-addresses", {"vmids": running}).payload.get("addresses") or {}
        except (HostAgentError, SetupError) as exc:
            logger.info("Setup: guest addresses unavailable (%s)", exc)

    probe_port = port_probe or _port_open
    all_addresses = sorted({address for values in addresses.values() for address in values})
    with ThreadPoolExecutor(max_workers=8) as pool:
        open_ports = dict(zip(all_addresses, pool.map(probe_port, all_addresses))) if all_addresses else {}

    candidates = []
    for vm in probe.vms:
        vm_addresses = list(addresses.get(str(vm["vmid"]), []))
        port_addresses = [address for address in vm_addresses if open_ports.get(address)]
        name_match = "pbs" in vm["name"].lower()
        score = (2 if port_addresses else 0) + (1 if name_match else 0)
        suggested_address = (port_addresses or (vm_addresses if name_match else []) or [None])[0]
        candidates.append(
            {
                **vm,
                "addresses": vm_addresses,
                "pbs_port_open": bool(port_addresses),
                "name_match": name_match,
                "score": score,
                "suggested_url": f"https://{suggested_address}:8007" if suggested_address else None,
            }
        )
    candidates.sort(key=lambda item: (-item["score"], item["vmid"]))
    suggested = candidates[0]["vmid"] if candidates and candidates[0]["score"] > 0 else None
    return {"node": settings.pve_node_name, "suggested_vmid": suggested, "vms": candidates}


PBS_PORT = 8007


def _pbs_answers(address: str, *, transport: httpx.BaseTransport | None = None) -> bool:
    """True when https://<address>:8007 answers like PBS (any HTTP status on
    /api2/json/version: 401 without credentials is the normal answer).
    No credential is sent; the certificate is checked later (fingerprint)."""
    host = f"[{address}]" if ":" in address else address
    try:
        with httpx.Client(verify=False, timeout=3.0, transport=transport) as client:  # noqa: S501 (no secret sent)
            response = client.get(f"https://{host}:{PBS_PORT}/api2/json/version")
    except httpx.HTTPError:
        return False
    return response.status_code in (200, 401, 403)


def detect_pbs_url(
    db: Session,
    vmid: int,
    *,
    agent_client: HostAgentClient | None = None,
    prober: Callable[[str], bool] | None = None,
) -> dict[str, Any]:
    """IP of the chosen PBS VM (host agent: guest agent first, then the MAC
    addresses of its NICs in the neighbour table), kept only if PBS answers
    on port 8007. `detected=False` = the operator types the address."""
    client = agent_client or host_agent_client(db)
    try:
        payload = client.post("/pve/vm-addresses", {"vmid": vmid}).payload
    except HostAgentError as exc:
        raise _agent_error(exc, "the PBS VM address lookup") from None
    candidates = [item for item in payload.get("addresses") or [] if isinstance(item, dict) and item.get("ip")]
    guest = _guest_agent(client, vmid)
    probe = prober or _pbs_answers
    for item in candidates:
        if probe(str(item["ip"])):
            return {
                "vmid": vmid,
                "detected": True,
                "ip": item["ip"],
                "source": item.get("source") or "",
                "url": f"https://{item['ip']}:{PBS_PORT}",
                "addresses": candidates,
                "guest_agent": guest,
            }
    return {"vmid": vmid, "detected": False, "ip": None, "source": None, "url": None, "addresses": candidates, "guest_agent": guest}


def _guest_agent(client: Any, vmid: int) -> dict[str, Any] | None:
    """B41: is the QEMU guest agent of the PBS VM active? (None: old host agent)."""
    from app.services.stale_mounts import guest_agent_commands

    try:
        payload = client.post("/pbs-guest/agent", {"vmid": vmid}).payload or {}
    except Exception:  # noqa: BLE001
        return None
    return {"enabled": bool(payload.get("enabled")), "active": bool(payload.get("active")), "commands": guest_agent_commands(vmid)}


def pbs_fingerprint(api_url: str, *, transport: httpx.BaseTransport | None = None) -> dict[str, Any]:
    """Certificate fingerprint seen on the PBS URL (no credential sent): the
    operator compares it with PBS › Dashboard › Show fingerprint."""
    from app.services.setup_probe import _trust_on_first_use

    try:
        url = normalize_api_url(api_url, 8007)
        result = _trust_on_first_use(url, "PBS", transport)
    except ProbeError as exc:
        raise SetupError("unreachable", f"{exc}. Check the address and port 8007.") from None
    return {"api_url": url, "fingerprint": result.fingerprint}


class FailureLimiter:
    """In-memory failures per client (per process), like the login limiter."""

    def __init__(self, max_failures: int, window_seconds: float) -> None:
        self.max_failures = max_failures
        self.window_seconds = window_seconds
        self._failures: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> list[float]:
        recent = [t for t in self._failures.get(key, []) if now - t < self.window_seconds]
        self._failures[key] = recent
        return recent

    def blocked(self, key: str, now: float | None = None) -> bool:
        with self._lock:
            return len(self._prune(key, now if now is not None else time.monotonic())) >= self.max_failures

    def record(self, key: str, now: float | None = None) -> None:
        with self._lock:
            self._prune(key, now if now is not None else time.monotonic()).append(
                now if now is not None else time.monotonic()
            )

    def reset(self) -> None:
        with self._lock:
            self._failures.clear()


# 5 failed provisioning attempts (wrong password...) per client per 15 minutes.
pbs_provision_limiter = FailureLimiter(5, 15 * 60)


def store_pbs_access(db: Session, result: Any, *, pbs_vmid: int | None) -> dict[str, Any]:
    settings = effective_settings(db)
    values: dict[str, Any] = {
        "PBS_API_URL": result.api_url,
        "PBS_TOKEN_ID": result.token_id,
        "PBS_TOKEN_SECRET": result.secret,
        "PBS_VERIFY_SSL": False,
        "PBS_FINGERPRINT": result.fingerprint,
        "PBS_MODE": "api",
    }
    if pbs_vmid:
        values["PBS_EXECUTION_VM_ID"] = pbs_vmid
        values["PBS_EXECUTION_VM_NODE"] = settings.pve_node_name
    update_settings(db, values)
    summary = {
        "api_url": result.api_url,
        "fingerprint": result.fingerprint,
        "version": result.version,
        "token_id": result.token_id,
        "vmid": pbs_vmid,
        "datastores": [store["name"] for store in result.datastores],
    }
    save_progress(db, data={"pbs": summary})
    logger.info("Setup: PBS connected (%s, %s, token %s)", result.api_url, result.version, result.token_id)
    return {**summary, "datastores": result.datastores}


def list_pbs_datastores(db: Session, *, transport: httpx.BaseTransport | None = None) -> list[dict[str, Any]]:
    """Datastores the stored PBS token can see (PBS filters the list by ACL)."""
    from app.services.setup_probe import probe_pbs

    settings = effective_settings(db)
    secret = stored_secret(db, "PBS_TOKEN_SECRET")
    if not (settings.pbs_api_url and settings.pbs_token_id and secret):
        raise SetupError("pbs_missing", "Connect PBS first.")
    try:
        probe = probe_pbs(
            settings.pbs_api_url, settings.pbs_token_id, secret,
            verify_ssl=settings.pbs_verify_ssl, fingerprint=settings.pbs_fingerprint, transport=transport,
        )
    except ProbeError as exc:
        raise SetupError("pbs_check_failed", f"Cannot list the PBS datastores: {exc}", status_code=502) from None
    return probe.datastores


def refresh_pbs_datastores(db: Session, *, transport: httpx.BaseTransport | None = None) -> list[dict[str, Any]]:
    datastores = list_pbs_datastores(db, transport=transport)
    progress = get_progress(db)
    pbs = dict(progress["data"].get("pbs") or {})
    if pbs:
        pbs["datastores"] = [store["name"] for store in datastores]
        save_progress(db, data={"pbs": pbs})
    return datastores


def select_pbs_datastore(db: Session, datastore: str, *, transport: httpx.BaseTransport | None = None) -> dict[str, Any]:
    names = [store["name"] for store in list_pbs_datastores(db, transport=transport)]
    if datastore not in names:
        raise SetupError(
            "unknown_datastore",
            f"Datastore `{datastore}` does not exist on PBS (or the token cannot see it). Available: {', '.join(names) or 'none'}.",
        )
    update_settings(db, {"PBS_DATASTORE": datastore})
    progress = get_progress(db)
    pbs = dict(progress["data"].get("pbs") or {})
    pbs["datastore"] = datastore
    save_progress(db, data={"pbs": pbs})
    return {"datastore": datastore}


def manual_commands() -> dict[str, list[str]]:
    from app.services.pbs_provisioning import PBS_ACLS, PBS_TOKEN_NAME, PBS_USER

    pbs_token = f"{PBS_USER}!{PBS_TOKEN_NAME}"
    pve_token = f"{PVE_USER}!{PVE_TOKEN_NAME}"
    pve = [
        f'pveum role add {PVE_ROLE} --privs "{",".join(PVE_PRIVILEGES)}"',
        f'pveum user add {PVE_USER} --comment "Proxmox Backup Orchestrator"',
        f"pveum user token add {PVE_USER} {PVE_TOKEN_NAME} --privsep 1",
        f"pveum acl modify / --users {PVE_USER} --roles {PVE_ROLE}",
        f"pveum acl modify / --tokens '{pve_token}' --roles {PVE_ROLE}",
        "openssl x509 -in /etc/pve/local/pve-ssl.pem -noout -fingerprint -sha256",
    ]
    pbs = [
        f'proxmox-backup-manager user create {PBS_USER} --comment "Proxmox Backup Orchestrator"',
        f"proxmox-backup-manager user generate-token {PBS_USER} {PBS_TOKEN_NAME}",
    ]
    for path, role in PBS_ACLS:
        pbs.append(f"proxmox-backup-manager acl update {path} {role} --auth-id {PBS_USER}")
        pbs.append(f"proxmox-backup-manager acl update {path} {role} --auth-id '{pbs_token}'")
    pbs.append("proxmox-backup-manager cert info | grep -i fingerprint")
    return {"pve": pve, "pbs": pbs, "pve_token_id": [pve_token], "pbs_token_id": [pbs_token]}
