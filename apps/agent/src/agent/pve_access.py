"""Proxmox VE access provisioning for the setup wizard (host role only).

`provision_pve_access()` creates (or updates) a dedicated API identity for the
orchestrator so the operator never creates or copies a token by hand:

* user `pbo@pve` (PVE realm: no password, it only owns the token);
* custom role `PBO` holding the minimum privileges the app uses
  (docs/PERMISSIONS.md explains each one);
* ACL `/` → role `PBO` for the user AND the token (privilege separation on);
* token `pbo@pve!pbo`, deleted then re-created on every run because PVE
  never returns an existing token secret.

It also returns the node name, PVE version, the API URL on the bridge address
the agent listens on, and the SHA-256 fingerprint of the PVE certificate read
from the local file — the app pins it without trust-on-first-use, since the
app ↔ agent channel is already pinned both ways.

The token secret is returned to the caller and never logged.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import logging
import re
import socket
import ssl
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger("agent.pve_access")

PBO_USER = "pbo@pve"
PBO_TOKEN_NAME = "pbo"
PBO_TOKEN_ID = f"{PBO_USER}!{PBO_TOKEN_NAME}"
PBO_ROLE = "PBO"
# Keep in sync with docs/PERMISSIONS.md (one line per privilege, with the API
# calls that need it). Order is irrelevant for pveum.
PBO_PRIVILEGES = (
    "Sys.Audit",  # GET /cluster/status, GET /cluster/backup[/{id}]
    "Sys.Modify",  # GET /nodes/{node}/hardware/usb, POST/PUT/DELETE /cluster/backup
    "VM.Audit",  # GET /nodes/{node}/qemu, /lxc (filtered per guest), qemu config
    "Datastore.Allocate",  # backup jobs: checked on /storage/<job storage>
)
PVE_CERT_FILES = (Path("/etc/pve/local/pveproxy-ssl.pem"), Path("/etc/pve/local/pve-ssl.pem"))
PVE_NODES_DIR = Path("/etc/pve/nodes")
QEMU_CONF_DIR = Path("/etc/pve/qemu-server")
PVEUM_TIMEOUT_SECONDS = 30.0
GUEST_AGENT_TIMEOUT_SECONDS = 6.0
MAX_GUEST_QUERIES = 32

Runner = Callable[[list[str], float], subprocess.CompletedProcess]


class ProvisionError(RuntimeError):
    pass


def _default_runner(command: list[str], timeout: float) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(command, check=False, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise ProvisionError(f"`{command[0]}` not found: is this a Proxmox VE host?") from exc
    except subprocess.TimeoutExpired as exc:
        raise ProvisionError(f"`{' '.join(command[:4])}` timed out after {timeout:.0f} s.") from exc


def _run(runner: Runner, command: list[str], *, step: str) -> str:
    result = runner(command, PVEUM_TIMEOUT_SECONDS)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()[:500]
        raise ProvisionError(f"{step} failed (`{' '.join(command[:4])}`): {detail or f'exit {result.returncode}'}")
    return result.stdout or ""


def _json_list(raw: str) -> list[dict[str, Any]]:
    try:
        data = json.loads(raw or "[]")
    except ValueError:
        return []
    return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []


def certificate_fingerprint_from_pem(pem: str) -> str:
    match = re.search(r"-----BEGIN CERTIFICATE-----.+?-----END CERTIFICATE-----", pem, re.DOTALL)
    if not match:
        raise ProvisionError("No certificate found in the PVE certificate file.")
    digest = hashlib.sha256(ssl.PEM_cert_to_DER_cert(match.group(0))).hexdigest().upper()
    return ":".join(digest[index : index + 2] for index in range(0, len(digest), 2))


def pve_certificate_fingerprint(cert_files: tuple[Path, ...] = PVE_CERT_FILES) -> str:
    """pveproxy serves pveproxy-ssl.pem when present (custom/ACME certificate),
    pve-ssl.pem otherwise."""
    for path in cert_files:
        if path.is_file():
            return certificate_fingerprint_from_pem(path.read_text(encoding="ascii", errors="replace"))
    raise ProvisionError("PVE certificate not found (/etc/pve/local/pve-ssl.pem).")


def node_name(nodes_dir: Path = PVE_NODES_DIR) -> str:
    name = socket.gethostname().split(".")[0]
    if nodes_dir.is_dir() and not (nodes_dir / name).is_dir():
        raise ProvisionError(f"Node `{name}` not found in {nodes_dir}: is this host part of the cluster?")
    return name


def parse_pveversion(output: str) -> str:
    match = re.search(r"pve-manager/([0-9][0-9A-Za-z.~+-]*)", output or "")
    return match.group(1) if match else ""


def listen_address(server_host: str, runner: Runner) -> str:
    """Address of the bridge the agent listens on (the installer binds vmbr0)."""
    try:
        address = ipaddress.ip_address(server_host)
        if not address.is_unspecified and not address.is_loopback:
            return str(address)
    except ValueError:
        pass
    result = runner(["ip", "-4", "-o", "addr", "show", "vmbr0"], 10.0)
    match = re.search(r"inet ([0-9.]+)/", result.stdout or "")
    if not match:
        raise ProvisionError("Cannot determine the Proxmox VE address (no IPv4 on vmbr0).")
    return match.group(1)


def provision_pve_access(
    server_host: str,
    *,
    runner: Runner | None = None,
    cert_files: tuple[Path, ...] = PVE_CERT_FILES,
    nodes_dir: Path = PVE_NODES_DIR,
) -> dict[str, Any]:
    run = runner or _default_runner
    privileges = ",".join(PBO_PRIVILEGES)

    users = _json_list(_run(run, ["pveum", "user", "list", "--output-format", "json"], step="List users"))
    if any(item.get("userid") == PBO_USER for item in users):
        _run(run, ["pveum", "user", "modify", PBO_USER, "--enable", "1"], step="Enable user")
        logger.info("PVE provisioning: user %s already exists", PBO_USER)
    else:
        _run(
            run,
            ["pveum", "user", "add", PBO_USER, "--comment", "Proxmox Backup Orchestrator (managed)"],
            step="Create user",
        )
        logger.info("PVE provisioning: user %s created", PBO_USER)

    roles = _json_list(_run(run, ["pveum", "role", "list", "--output-format", "json"], step="List roles"))
    if any(item.get("roleid") == PBO_ROLE for item in roles):
        # Without --append, `role modify --privs` replaces the privilege list.
        _run(run, ["pveum", "role", "modify", PBO_ROLE, "--privs", privileges], step="Update role")
    else:
        _run(run, ["pveum", "role", "add", PBO_ROLE, "--privs", privileges], step="Create role")
    logger.info("PVE provisioning: role %s = %s", PBO_ROLE, privileges)

    tokens = _json_list(
        _run(run, ["pveum", "user", "token", "list", PBO_USER, "--output-format", "json"], step="List tokens")
    )
    if any(item.get("tokenid") == PBO_TOKEN_NAME for item in tokens):
        _run(run, ["pveum", "user", "token", "remove", PBO_USER, PBO_TOKEN_NAME], step="Remove previous token")
        logger.info("PVE provisioning: previous token %s removed", PBO_TOKEN_ID)
    raw = _run(
        run,
        [
            "pveum", "user", "token", "add", PBO_USER, PBO_TOKEN_NAME,
            "--privsep", "1",
            "--comment", "Proxmox Backup Orchestrator",
            "--output-format", "json",
        ],
        step="Create token",
    )
    try:
        created = json.loads(raw)
        secret = str(created["value"])
        token_id = str(created.get("full-tokenid") or PBO_TOKEN_ID)
    except (ValueError, KeyError, TypeError) as exc:
        raise ProvisionError("Unexpected output from `pveum user token add` (no token value).") from exc
    logger.info("PVE provisioning: token %s created", token_id)

    _run(
        run,
        ["pveum", "acl", "modify", "/", "--users", PBO_USER, "--roles", PBO_ROLE, "--propagate", "1"],
        step="Set user ACL",
    )
    _run(
        run,
        ["pveum", "acl", "modify", "/", "--tokens", token_id, "--roles", PBO_ROLE, "--propagate", "1"],
        step="Set token ACL",
    )
    logger.info("PVE provisioning: ACL / -> %s for %s and %s", PBO_ROLE, PBO_USER, token_id)

    version_result = run(["pveversion"], 15.0)
    address = listen_address(server_host, run)
    host = f"[{address}]" if ":" in address else address
    return {
        "ok": True,
        "message": "Proxmox VE access provisioned.",
        "token_id": token_id,
        "secret": secret,
        "node": node_name(nodes_dir),
        "pve_version": parse_pveversion(version_result.stdout or ""),
        "api_url": f"https://{host}:8006",
        "fingerprint": pve_certificate_fingerprint(cert_files),
        "role": PBO_ROLE,
        "privileges": list(PBO_PRIVILEGES),
        "command_summary": "pveum user/role/token/acl (pbo@pve!pbo)",
    }


# --- Guest addresses (PBS VM detection) ------------------------------------

_AGENT_ENABLED_RE = re.compile(r"^agent:\s*(?:1\b|.*\benabled=1\b|enabled=1)", re.MULTILINE)


def guest_agent_enabled(vmid: int, conf_dir: Path = QEMU_CONF_DIR) -> bool:
    path = conf_dir / f"{vmid}.conf"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    current = text.split("\n[", 1)[0]  # ignore snapshot sections
    return bool(_AGENT_ENABLED_RE.search(current))


def parse_guest_ipv4(raw: str) -> list[str]:
    try:
        interfaces = json.loads(raw or "[]")
    except ValueError:
        return []
    addresses: list[str] = []
    for interface in interfaces if isinstance(interfaces, list) else []:
        if not isinstance(interface, dict) or interface.get("name") == "lo":
            continue
        for entry in interface.get("ip-addresses") or []:
            if not isinstance(entry, dict) or entry.get("ip-address-type") != "ipv4":
                continue
            value = str(entry.get("ip-address") or "")
            try:
                address = ipaddress.ip_address(value)
            except ValueError:
                continue
            if not (address.is_loopback or address.is_link_local) and value not in addresses:
                addresses.append(value)
    return addresses


def guest_addresses(
    vmids: list[int],
    *,
    runner: Runner | None = None,
    conf_dir: Path = QEMU_CONF_DIR,
) -> dict[str, Any]:
    """IPv4 addresses reported by the QEMU guest agent (root on the host: no
    VM.GuestAgent.* privilege needed in the PBO role)."""
    run = runner or _default_runner
    selected = [vmid for vmid in dict.fromkeys(vmids) if vmid > 0][:MAX_GUEST_QUERIES]

    def query(vmid: int) -> tuple[int, list[str]]:
        if not guest_agent_enabled(vmid, conf_dir):
            return vmid, []
        try:
            result = run(["qm", "guest", "cmd", str(vmid), "network-get-interfaces"], GUEST_AGENT_TIMEOUT_SECONDS)
        except ProvisionError:
            return vmid, []
        return vmid, parse_guest_ipv4(result.stdout) if result.returncode == 0 else []

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = dict(pool.map(query, selected))
    return {
        "ok": True,
        "message": "Guest addresses collected.",
        "addresses": {str(vmid): results.get(vmid, []) for vmid in selected},
    }
