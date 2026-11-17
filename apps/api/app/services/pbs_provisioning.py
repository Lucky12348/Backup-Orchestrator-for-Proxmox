"""PBS API access provisioning for the setup wizard.

The operator types the root@pam password ONCE; it is used for a single PBS
ticket over a connection pinned on the fingerprint they confirmed, then
dropped. It is never stored, logged, echoed in an error, or kept on an
exception (errors are raised `from None` and only carry PBS's own message).

With that ticket the app creates (or updates):

* user `pbo@pbs` (PBS realm, no password: it only owns the token);
* token `pbo@pbs!pbo` — deleted then re-created on every run, PBS never
  returns an existing secret;
* the ACLs of `PBS_ACLS`, for the user AND the token (a PBS token's
  privileges are the intersection of its own ACLs and its user's).

PBS has no custom roles: each path gets the smallest built-in role holding
the privileges listed in docs/PBS_API.md (see docs/PERMISSIONS.md).
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

import httpx

from app.core.tls import build_tls_verify
from app.services.setup_probe import (
    ProbeError,
    normalize_api_url,
    normalize_fingerprint,
    probe_pbs,
)

logger = logging.getLogger(__name__)

PBS_USER = "pbo@pbs"
PBS_TOKEN_NAME = "pbo"
PBS_TOKEN_ID = f"{PBS_USER}!{PBS_TOKEN_NAME}"
# (path, built-in role). Keep in sync with docs/PERMISSIONS.md and docs/PBS_API.md.
PBS_ACLS: tuple[tuple[str, str], ...] = (
    # Sys.Audit + Sys.Modify: disk list, wipe, create removable datastore,
    # mount/unmount. Only `Admin` holds Sys.Modify among the built-in roles.
    ("/system/disks", "Admin"),
    # Sys.Audit: list and follow tasks that are not the token's own.
    ("/system/tasks", "Audit"),
    # Datastore.Allocate (POST /config/datastore: re-create a deleted config
    # with reuse-datastore) + Modify/Audit/Read/Backup/Prune/Verify on every
    # store. Only `Admin` holds Datastore.Allocate (DatastoreAdmin does not);
    # the target stores are created at runtime, so the ACL sits on the parent
    # path (it survives `datastore remove`, which drops /datastore/<name>).
    # Details: services/pbs_permissions.py, docs/PERMISSIONS.md.
    ("/datastore", "Admin"),
)
PBS_MIN_VERSION = (3, 3)
PROVISION_TIMEOUT_SECONDS = 20.0
# PBS caches its ACL config for a few seconds: right after `PUT access/acl` the
# new token may still have no rights. Then GET /admin/datastore is *filtered*
# (an empty list, not a 403) and other calls answer 403. The check is retried
# with these waits (10 s in total) before concluding "no datastore".
ACL_PROPAGATION_DELAYS: tuple[float, ...] = (0.5, 1.0, 2.0, 3.0, 3.5)


class PbsProvisionError(RuntimeError):
    """`code` is stable for the UI; the message is safe to show (no secret)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class PbsProvisionResult:
    api_url: str
    fingerprint: str
    version: str
    token_id: str
    secret: str = field(repr=False)
    datastores: list[dict[str, Any]]


def parse_version(value: str) -> tuple[int, int]:
    match = re.match(r"\s*(\d+)\.(\d+)", value or "")
    return (int(match.group(1)), int(match.group(2))) if match else (0, 0)


def version_supported(value: str) -> bool:
    return parse_version(value) >= PBS_MIN_VERSION


def _pbs_message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text.strip()[:300]
    if isinstance(body, dict):
        parts = [str(body.get("message") or "").strip()]
        errors = body.get("errors")
        if isinstance(errors, dict):
            parts.append("; ".join(f"{key}: {value}" for key, value in errors.items()))
        return " — ".join(part for part in parts if part)[:300]
    return ""


class _PbsSession:
    def __init__(self, api_url: str, fingerprint: str, transport: httpx.BaseTransport | None) -> None:
        self.client = httpx.Client(
            base_url=api_url.rstrip("/") + "/",
            verify=build_tls_verify(False, fingerprint),
            timeout=PROVISION_TIMEOUT_SECONDS,
            transport=transport,
        )

    def close(self) -> None:
        self.client.close()

    def login(self, username: str, password: str) -> None:
        try:
            response = self.client.post("access/ticket", data={"username": username, "password": password})
        except httpx.HTTPError as exc:
            # Never chain: the request object holds the password.
            raise _transport_error(exc) from None
        if response.status_code in (400, 401, 403):
            raise PbsProvisionError(
                "bad_credentials",
                f"PBS refused the login for {username}: check the password (and that the user is root@pam).",
            ) from None
        if response.is_error:
            raise PbsProvisionError("pbs_error", f"PBS login failed (HTTP {response.status_code}).") from None
        data = (response.json() or {}).get("data") or {}
        ticket, csrf = data.get("ticket"), data.get("CSRFPreventionToken")
        if not ticket or not csrf:
            raise PbsProvisionError("pbs_error", "PBS login returned no ticket.") from None
        self.client.headers["Cookie"] = f"PBSAuthCookie={ticket}"
        self.client.headers["CSRFPreventionToken"] = csrf

    def call(self, method: str, path: str, *, step: str, data: dict[str, Any] | None = None) -> Any:
        try:
            response = self.client.request(method, path, data=data)
        except httpx.HTTPError as exc:
            raise _transport_error(exc) from None
        if response.is_error:
            detail = _pbs_message(response)
            raise PbsProvisionError(
                "pbs_error", f"{step} failed on PBS (HTTP {response.status_code}{': ' + detail if detail else ''})."
            )
        try:
            payload = response.json()
        except ValueError:
            return None
        return payload.get("data") if isinstance(payload, dict) else payload


def _transport_error(exc: httpx.HTTPError) -> PbsProvisionError:
    text = str(exc)
    if "fingerprint mismatch" in text:
        return PbsProvisionError(
            "fingerprint_mismatch",
            "The PBS certificate does not match the confirmed fingerprint: nothing was sent. Check the URL, then verify again.",
        )
    return PbsProvisionError("unreachable", f"PBS is unreachable ({exc.__class__.__name__}): check the URL and port 8007.")


def _probe_new_token(
    url: str,
    token_id: str,
    secret: str,
    fingerprint: str,
    *,
    transport: httpx.BaseTransport | None,
    delays: tuple[float, ...] | None = None,
    sleep=None,  # noqa: ANN001  (tests patch time.sleep)
):
    """End-to-end check with the new token (pinned) + the datastore list,
    retried while PBS still serves its cached ACLs (403 or an empty list)."""
    waits = ACL_PROPAGATION_DELAYS if delays is None else delays
    wait = sleep or time.sleep
    probe = None
    last_error: ProbeError | None = None
    for attempt in range(len(waits) + 1):
        if attempt:
            wait(waits[attempt - 1])
        try:
            probe = probe_pbs(url, token_id, secret, verify_ssl=False, fingerprint=fingerprint, transport=transport)
            last_error = None
        except ProbeError as exc:
            if exc.code != "token_rejected":  # unreachable, fingerprint mismatch...: no point waiting
                raise PbsProvisionError("token_check_failed", f"The new token does not work: {exc}") from None
            last_error = exc
            continue
        if probe.datastores:
            return probe
        logger.info("PBS provisioning: no datastore visible yet (attempt %d), waiting for the ACL cache", attempt + 1)
    if probe is None:
        raise PbsProvisionError(
            "token_check_failed", f"The new token does not work yet: {last_error}"
        ) from None
    return probe  # really no datastore (or still invisible after ~10 s)


def provision_pbs_access(
    api_url: str,
    fingerprint: str,
    password: str,
    *,
    username: str = "root@pam",
    transport: httpx.BaseTransport | None = None,
) -> PbsProvisionResult:
    try:
        url = normalize_api_url(api_url, 8007)
        pinned = normalize_fingerprint(fingerprint)
    except ProbeError as exc:
        raise PbsProvisionError("invalid_input", str(exc)) from None
    if not pinned:
        raise PbsProvisionError("invalid_input", "Verify the PBS certificate fingerprint first.")
    if not password:
        raise PbsProvisionError("invalid_input", "The root@pam password is required.")

    session = _PbsSession(url, pinned, transport)
    try:
        session.login(username, password)
        password = ""
        version = str((session.call("GET", "version", step="Version check") or {}).get("version", ""))
        if not version_supported(version):
            raise PbsProvisionError(
                "pbs_too_old",
                f"PBS {version or '?'} is too old: the API mode needs removable datastores (PBS 3.3 or later). "
                "Upgrade PBS, then retry.",
            )

        users = session.call("GET", "access/users", step="List users") or []
        if any(isinstance(item, dict) and item.get("userid") == PBS_USER for item in users):
            session.call("PUT", f"access/users/{quote(PBS_USER)}", step="Enable user", data={"enable": 1})
        else:
            session.call(
                "POST",
                "access/users",
                step="Create user",
                data={"userid": PBS_USER, "comment": "Proxmox Backup Orchestrator (managed)", "enable": 1},
            )
        logger.info("PBS provisioning: user %s ready", PBS_USER)

        token_path = f"access/users/{quote(PBS_USER)}/token"
        tokens = session.call("GET", token_path, step="List tokens") or []
        if any(
            isinstance(item, dict) and item.get("tokenid") in (PBS_TOKEN_ID, PBS_TOKEN_NAME) for item in tokens
        ):
            session.call("DELETE", f"{token_path}/{PBS_TOKEN_NAME}", step="Remove previous token")
            logger.info("PBS provisioning: previous token %s removed", PBS_TOKEN_ID)
        created = session.call(
            "POST", f"{token_path}/{PBS_TOKEN_NAME}", step="Create token", data={"comment": "Proxmox Backup Orchestrator"}
        ) or {}
        secret = str(created.get("value") or "")
        token_id = str(created.get("tokenid") or PBS_TOKEN_ID)
        if not secret:
            raise PbsProvisionError("pbs_error", "PBS created the token but returned no secret.")
        logger.info("PBS provisioning: token %s created", token_id)

        for path, role in PBS_ACLS:
            for auth_id in (PBS_USER, token_id):
                session.call(
                    "PUT",
                    "access/acl",
                    step=f"ACL {path}",
                    data={"path": path, "role": role, "auth-id": auth_id, "propagate": 1},
                )
        logger.info("PBS provisioning: ACLs set for %s and %s", PBS_USER, token_id)
    finally:
        session.close()

    probe = _probe_new_token(url, token_id, secret, pinned, transport=transport)
    return PbsProvisionResult(
        api_url=url,
        fingerprint=pinned,
        version=version,
        token_id=token_id,
        secret=secret,
        datastores=probe.datastores,
    )
