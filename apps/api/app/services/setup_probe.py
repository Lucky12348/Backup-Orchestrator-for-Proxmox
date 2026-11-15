"""Connection probes used by the setup wizard (Proxmox VE and PBS).

A probe takes the URL + API token typed by the operator, independently of the
stored settings, and reports what the token can see plus the SHA-256
fingerprint of the server certificate. TLS is handled by the same
`PinnedSSLContext` as the runtime clients, inside the handshake of the very
connection that carries the token — there is no separate "check, then
connect" step an attacker could slip between:

* verify_ssl=True                → CA verification (fingerprint recorded).
* fingerprint given              → the certificate must match it, checked in the
                                   handshake before the token is sent.
* verify_ssl=False, no pin       → trust on first use: only an unauthenticated
                                   request is made, the token is NOT sent, and the
                                   observed fingerprint is returned with
                                   `trust_required=True` for the operator to pin.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.core.tls import FingerprintFormatError, build_tls_verify
from app.core.tls import normalize_fingerprint as _normalize_fingerprint

PROBE_TIMEOUT_SECONDS = 10.0
PBS_NODE = "localhost"


class ProbeError(RuntimeError):
    """`code` is stable (the UI translates it); the message is the English detail."""

    def __init__(self, message: str, *, code: str = "probe_failed") -> None:
        super().__init__(message)
        self.code = code


def normalize_fingerprint(value: str | None) -> str:
    try:
        return _normalize_fingerprint(value)
    except FingerprintFormatError as exc:
        raise ProbeError(str(exc), code="invalid_fingerprint") from exc


def normalize_api_url(url: str, default_port: int) -> str:
    raw = (url or "").strip()
    if not raw:
        raise ProbeError("API URL is required.", code="invalid_url")
    if "://" not in raw:
        raw = f"https://{raw}"
    parts = urlsplit(raw)
    if parts.scheme != "https" or not parts.hostname:
        raise ProbeError("API URL must be an https:// URL with a host name or address.", code="invalid_url")
    port = parts.port or default_port
    host = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname
    return f"https://{host}:{port}/api2/json"


@dataclass
class ProbeResult:
    api_url: str
    version: str
    fingerprint: str
    fingerprint_pinned: bool
    trust_required: bool = False
    nodes: list[str] = field(default_factory=list)
    vms: list[dict[str, Any]] = field(default_factory=list)
    containers: list[dict[str, Any]] = field(default_factory=list)
    datastores: list[dict[str, Any]] = field(default_factory=list)


class _Session:
    def __init__(self, api_url: str, auth_header: str | None, verify_ssl: bool, pinned: str, transport) -> None:  # noqa: ANN001
        self.observed: list[str] = []
        self.pinned = pinned
        headers = {"Authorization": auth_header} if auth_header else {}
        self.client = httpx.Client(
            base_url=api_url.rstrip("/") + "/",
            headers=headers,
            verify=build_tls_verify(verify_ssl, pinned, observed=self.observed),
            timeout=PROBE_TIMEOUT_SECONDS,
            transport=transport,
        )

    def __enter__(self) -> "_Session":
        return self

    def __exit__(self, *exc: object) -> None:
        self.client.close()

    @property
    def fingerprint(self) -> str:
        return self.observed[0] if self.observed else self.pinned

    def get(self, path: str, product: str, *, authenticated: bool = True) -> Any:
        try:
            response = self.client.get(path)
        except httpx.HTTPError as exc:
            if "fingerprint mismatch" in str(exc):
                raise ProbeError(f"{exc}. Refusing to send the API token.", code="fingerprint_mismatch") from exc
            raise ProbeError(f"{product} API unreachable: {exc}", code="unreachable") from exc
        if not authenticated:
            return None
        if response.status_code in (401, 403):
            raise ProbeError(
                f"{product} rejected the API token (HTTP {response.status_code}) on {path}. "
                "Check token id, secret and permissions.",
                code="token_rejected",
            )
        if response.is_error:
            raise ProbeError(f"{product} API error on {path}: HTTP {response.status_code}", code="api_error")
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProbeError(f"{product} API returned a non-JSON response on {path}.", code="api_error") from exc
        return payload.get("data", payload) if isinstance(payload, dict) else payload


def _trust_on_first_use(api_url: str, product: str, transport) -> ProbeResult:  # noqa: ANN001
    with _Session(api_url, None, False, "", transport) as session:
        session.get("version", product, authenticated=False)
        return ProbeResult(
            api_url=api_url,
            version="",
            fingerprint=session.fingerprint,
            fingerprint_pinned=False,
            trust_required=True,
        )


def probe_proxmox(
    api_url: str,
    token_id: str,
    token_secret: str,
    *,
    verify_ssl: bool,
    fingerprint: str | None = None,
    transport: httpx.BaseTransport | None = None,
    include_containers: bool = False,
) -> ProbeResult:
    url = normalize_api_url(api_url, 8006)
    if not token_id.strip() or not token_secret:
        raise ProbeError("Proxmox API token id and secret are required.", code="secret_required")
    pinned = normalize_fingerprint(fingerprint)
    if not verify_ssl and not pinned:
        return _trust_on_first_use(url, "Proxmox VE", transport)
    header = f"PVEAPIToken={token_id.strip()}={token_secret}"
    with _Session(url, header, verify_ssl, pinned, transport) as session:
        version = session.get("version", "Proxmox VE")
        nodes_payload = session.get("nodes", "Proxmox VE") or []
        nodes = sorted(str(item.get("node")) for item in nodes_payload if item.get("node"))
        vms: list[dict[str, Any]] = []
        for node in nodes:
            for item in session.get(f"nodes/{node}/qemu", "Proxmox VE") or []:
                vms.append(
                    {
                        "vmid": int(item.get("vmid")),
                        "name": item.get("name") or "",
                        "node": node,
                        "status": item.get("status") or "unknown",
                    }
                )
        containers: list[dict[str, Any]] = []
        if include_containers:
            for node in nodes:
                for item in session.get(f"nodes/{node}/lxc", "Proxmox VE") or []:
                    containers.append(
                        {
                            "vmid": int(item.get("vmid")),
                            "name": item.get("name") or "",
                            "node": node,
                            "status": item.get("status") or "unknown",
                        }
                    )
        return ProbeResult(
            api_url=url,
            version=str((version or {}).get("version", "")),
            fingerprint=session.fingerprint,
            fingerprint_pinned=bool(pinned),
            nodes=nodes,
            vms=sorted(vms, key=lambda vm: vm["vmid"]),
            containers=sorted(containers, key=lambda ct: ct["vmid"]),
        )


def probe_pbs(
    api_url: str,
    token_id: str,
    token_secret: str,
    *,
    verify_ssl: bool,
    fingerprint: str | None = None,
    transport: httpx.BaseTransport | None = None,
) -> ProbeResult:
    url = normalize_api_url(api_url, 8007)
    if not token_id.strip() or not token_secret:
        raise ProbeError("PBS API token id and secret are required.", code="secret_required")
    pinned = normalize_fingerprint(fingerprint)
    if not verify_ssl and not pinned:
        return _trust_on_first_use(url, "PBS", transport)
    header = f"PBSAPIToken={token_id.strip()}:{token_secret}"
    with _Session(url, header, verify_ssl, pinned, transport) as session:
        # Only routes a token can reach. GET /nodes is NOT one of them: PBS's
        # list_nodes has no `access` block, so it is superuser-only and answers
        # 403 to any API token whatever its ACLs. The PBS node is always "localhost".
        version = session.get("version", "PBS")
        datastores_payload = session.get("admin/datastore", "PBS") or []
        fingerprint_seen = session.fingerprint
    datastores = [
        {
            "name": item.get("store") or item.get("name"),
            "comment": item.get("comment") or "",
            "maintenance": item.get("maintenance") or None,
            "mount_status": item.get("mount-status"),
        }
        for item in datastores_payload
        if item.get("store") or item.get("name")
    ]
    return ProbeResult(
        api_url=url,
        version=str((version or {}).get("version", "")),
        fingerprint=fingerprint_seen,
        fingerprint_pinned=bool(pinned),
        nodes=[PBS_NODE],
        datastores=sorted(datastores, key=lambda store: store["name"]),
    )
