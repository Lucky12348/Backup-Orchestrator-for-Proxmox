"""Proxmox Backup Server REST client.

Endpoints, parameters and return types are checked against the PBS 4.x API
schema (https://pbs.proxmox.com/docs/api-viewer/apidoc.js); see docs/PBS_API.md.

Every connection goes through `build_tls_verify`: when a fingerprint is
stored, the server certificate is checked during the TLS handshake of each
request, before the API token is sent.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import quote

import httpx

from app.core.config import Settings, get_settings
from app.core.tls import build_tls_verify

# PBS answers on its own node as "localhost" ("Node name (or 'localhost')").
PBS_NODE = "localhost"
UPID_PATTERN = re.compile(r"^UPID:[^:]+:[0-9A-Fa-f]{8}:")
TASK_LOG_PAGE = 500


class PBSApiError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class PBSTaskError(RuntimeError):
    def __init__(self, message: str, *, upid: str, exitstatus: str | None, log_lines: list[str]) -> None:
        super().__init__(message)
        self.upid = upid
        self.exitstatus = exitstatus
        self.log_lines = log_lines


class PBSTaskTimeout(RuntimeError):
    def __init__(self, message: str, *, upid: str, log_lines: list[str]) -> None:
        super().__init__(message)
        self.upid = upid
        self.log_lines = log_lines


def is_upid(value: Any) -> bool:
    return isinstance(value, str) and bool(UPID_PATTERN.match(value))


def parse_upid(upid: str) -> dict[str, str]:
    """UPID:node:pid:pstart:task_id:starttime:worker_type:worker_id:auth_id:"""
    parts = upid.split(":")
    if len(parts) < 9:
        return {}
    return {
        "node": parts[1],
        "worker_type": parts[6],
        # PBS escapes ':' inside the worker id as '\x3a'.
        "worker_id": parts[7].replace("\\x3a", ":"),
        "auth_id": parts[8],
    }


def task_worker_tokens(task: Mapping[str, Any]) -> set[str]:
    """Exact ':'-separated segments of a task's worker id (from the task or its UPID)."""
    raw = str(task.get("worker_id") or parse_upid(str(task.get("upid") or "")).get("worker_id") or "")
    return {token for token in raw.replace("\\x3a", ":").split(":") if token}


def task_succeeded(exitstatus: str | None) -> bool:
    """'OK' or 'WARNINGS: n' are successful outcomes; anything else is a failure."""
    status_text = (exitstatus or "").strip()
    return status_text == "OK" or status_text.upper().startswith("WARNINGS")


class PBSClient:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.settings = settings or get_settings()
        self._transport = transport
        self._sleep = sleep
        self._monotonic = monotonic

    @property
    def headers(self) -> Mapping[str, str]:
        return {
            "Authorization": (
                f"PBSAPIToken={self.settings.pbs_token_id}:{self.settings.pbs_token_secret}"
            )
        }

    def _client(self) -> httpx.Client:
        if not self.settings.pbs_token_id or not self.settings.pbs_token_secret:
            raise RuntimeError("PBS API token credentials are not configured")

        return httpx.Client(
            base_url=self.settings.pbs_api_url.rstrip("/") + "/",
            headers=self.headers,
            verify=build_tls_verify(self.settings.pbs_verify_ssl, self.settings.pbs_fingerprint),
            timeout=30.0,
            transport=self._transport,
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        body: Mapping[str, Any] | None = None,
    ) -> Any:
        with self._client() as client:
            try:
                response = client.request(method, path.lstrip("/"), params=params, json=body)
            except httpx.HTTPError as exc:
                raise PBSApiError(f"PBS API unreachable ({method} /{path.lstrip('/')}): {exc}") from exc
        if response.status_code == 401:
            raise PBSApiError(
                "PBS authentication failed. Check PBS token id/secret, token ACLs, and datastore permissions.",
                status_code=401,
            )
        if response.is_error:
            raise PBSApiError(_describe_error(method, path, response), status_code=response.status_code)
        try:
            payload = response.json()
        except ValueError:
            return None
        return payload.get("data", payload) if isinstance(payload, dict) else payload

    def _get(self, path: str, params: Mapping[str, Any] | None = None) -> Any:
        return self._request("GET", path, params=params)

    # --- read-only (inventory) ------------------------------------------------
    def get_version(self) -> Mapping[str, str]:
        data = self._get("version")
        return data if isinstance(data, Mapping) else {}

    def list_datastores(self) -> list[dict]:
        """GET /config/datastore — configured datastores (incl. `backing-device`)."""
        data = self._get("config/datastore")
        return data if isinstance(data, list) else []

    def list_snapshots(self, datastore_name: str) -> list[dict]:
        data = self._get(f"admin/datastore/{_seg(datastore_name)}/snapshots")
        return data if isinstance(data, list) else []

    # --- disks ----------------------------------------------------------------
    def list_disks(self, *, include_partitions: bool = True) -> list[dict]:
        """GET /nodes/{node}/disks/list (Sys.Audit on /system/disks)."""
        data = self._get(
            f"nodes/{PBS_NODE}/disks/list",
            params={"include-partitions": _flag(include_partitions), "skipsmart": _flag(True)},
        )
        return data if isinstance(data, list) else []

    def wipe_disk(self, disk: str) -> str:
        """PUT /nodes/{node}/disks/wipedisk → UPID (Sys.Modify on /system/disks)."""
        return _require_upid(self._request("PUT", f"nodes/{PBS_NODE}/disks/wipedisk", body={"disk": disk}), "wipedisk")

    def create_directory_datastore(self, *, disk: str, name: str, filesystem: str = "ext4", removable: bool = True) -> str:
        """POST /nodes/{node}/disks/directory with add-datastore → UPID.

        Formats the whole disk (one partition), creates the filesystem and the
        datastore `name`; with removable-datastore the datastore is bound to
        the filesystem UUID (`backing-device`) instead of a systemd mount unit.
        """
        body = {
            "disk": disk,
            "name": name,
            "filesystem": filesystem,
            "add-datastore": True,
            "removable-datastore": removable,
        }
        return _require_upid(self._request("POST", f"nodes/{PBS_NODE}/disks/directory", body=body), "disks/directory")

    # --- datastores -----------------------------------------------------------
    def get_datastore_config(self, name: str) -> dict:
        data = self._get(f"config/datastore/{_seg(name)}")
        return data if isinstance(data, dict) else {}

    def create_datastore(
        self,
        *,
        name: str,
        path: str,
        backing_device: str | None = None,
        reuse_datastore: bool = False,
        comment: str | None = None,
    ) -> str | None:
        """POST /config/datastore. Schema return type is null; a UPID is waited for if one comes back."""
        body: dict[str, Any] = {"name": name, "path": path}
        if backing_device:
            body["backing-device"] = backing_device
        if reuse_datastore:
            body["reuse-datastore"] = True
        if comment:
            body["comment"] = comment
        data = self._request("POST", "config/datastore", body=body)
        return data if is_upid(data) else None

    def update_datastore(self, name: str, **fields: Any) -> None:
        """PUT /config/datastore/{name} (Datastore.Modify)."""
        self._request("PUT", f"config/datastore/{_seg(name)}", body={key.replace("_", "-"): value for key, value in fields.items()})

    def list_admin_datastores(self) -> list[dict]:
        """GET /admin/datastore — includes `mount-status` (mounted|notmounted|nonremovable) and `maintenance`."""
        data = self._get("admin/datastore")
        return data if isinstance(data, list) else []

    def datastore_status(self, store: str) -> dict:
        """GET /admin/datastore/{store}/status — total/used/avail bytes."""
        data = self._get(f"admin/datastore/{_seg(store)}/status")
        return data if isinstance(data, dict) else {}

    def mount_datastore(self, store: str) -> str:
        """POST /admin/datastore/{store}/mount → UPID (removable datastores only)."""
        return _require_upid(self._request("POST", f"admin/datastore/{_seg(store)}/mount"), "mount")

    def unmount_datastore(self, store: str) -> str:
        """POST /admin/datastore/{store}/unmount → UPID."""
        return _require_upid(self._request("POST", f"admin/datastore/{_seg(store)}/unmount"), "unmount")

    # --- sync jobs --------------------------------------------------------------
    def list_sync_jobs(self) -> list[dict]:
        data = self._get("config/sync")
        return data if isinstance(data, list) else []

    def create_local_sync_job(
        self,
        *,
        job_id: str,
        store: str,
        remote_store: str,
        owner: str | None = None,
        remove_vanished: bool = False,
        comment: str | None = None,
        group_filter: list[str] | None = None,
    ) -> None:
        """POST /config/sync WITHOUT `remote`: local pull from `remote-store` into `store`.
        `group_filter` = ["group:vm/100", …] (only those VM/CT groups)."""
        body: dict[str, Any] = {
            "id": job_id,
            "store": store,
            "remote-store": remote_store,
            "remove-vanished": remove_vanished,
        }
        if owner:
            body["owner"] = owner
        if comment:
            body["comment"] = comment
        if group_filter:
            body["group-filter"] = list(group_filter)
        self._request("POST", "config/sync", body=body)

    def delete_sync_job(self, job_id: str) -> None:
        self._request("DELETE", f"config/sync/{_seg(job_id)}")

    def run_sync_job(self, job_id: str) -> str | None:
        """POST /admin/sync/{id}/run. The schema documents a null return; use find_task_upid() then."""
        data = self._request("POST", f"admin/sync/{_seg(job_id)}/run")
        return data if is_upid(data) else None

    # --- tasks ------------------------------------------------------------------
    def list_tasks(
        self,
        *,
        running: bool | None = None,
        store: str | None = None,
        typefilter: str | None = None,
        since: int | None = None,
        limit: int = 50,
    ) -> list[dict]:
        params: dict[str, Any] = {"limit": limit}
        if running is not None:
            params["running"] = _flag(running)
        if store:
            params["store"] = store
        if typefilter:
            params["typefilter"] = typefilter
        if since is not None:
            params["since"] = since
        data = self._get(f"nodes/{PBS_NODE}/tasks", params=params)
        return data if isinstance(data, list) else []

    def stop_task(self, upid: str) -> None:
        """DELETE /nodes/{node}/tasks/{upid}: ask PBS to abort a running task."""
        self._request("DELETE", f"nodes/{PBS_NODE}/tasks/{_seg(upid)}")

    def task_status(self, upid: str) -> dict:
        data = self._get(f"nodes/{PBS_NODE}/tasks/{_seg(upid)}/status")
        return data if isinstance(data, dict) else {}

    def task_log(self, upid: str, *, start: int = 0, limit: int = TASK_LOG_PAGE) -> list[str]:
        """GET /nodes/{node}/tasks/{upid}/log. The schema leaves items untyped:
        PBS returns [{"n": line_no, "t": text}]; plain strings are accepted too."""
        data = self._get(f"nodes/{PBS_NODE}/tasks/{_seg(upid)}/log", params={"start": start, "limit": limit})
        lines: list[str] = []
        for item in data if isinstance(data, list) else []:
            if isinstance(item, dict) and "t" in item:
                lines.append(str(item["t"]))
            elif isinstance(item, str):
                lines.append(item)
        return lines

    def find_task_upid(
        self,
        *,
        worker_type: str,
        worker_id_contains: str,
        since: int,
        attempts: int = 10,
        delay_seconds: float = 1.0,
    ) -> str | None:
        """Locate a task started after `since` whose worker id mentions `worker_id_contains`."""
        for _ in range(attempts):
            for task in self.list_tasks(typefilter=worker_type, since=since, limit=100):
                upid = task.get("upid")
                if not is_upid(upid):
                    continue
                info = parse_upid(upid)
                if info.get("worker_type", task.get("worker_type")) == worker_type and worker_id_contains in task_worker_tokens(task):
                    return upid
            self._sleep(delay_seconds)
        return None

    def wait_for_task(
        self,
        upid: str,
        *,
        timeout_seconds: float,
        poll_interval: float = 2.0,
        on_line: Callable[[str], None] | None = None,
    ) -> dict:
        """Poll the task until it stops, streaming new log lines to `on_line`.

        Raises PBSTaskTimeout (task still running, NOT stopped by us) or
        PBSTaskError (stopped with an exit status other than OK / WARNINGS).
        """
        deadline = self._monotonic() + timeout_seconds
        lines: list[str] = []

        def drain() -> None:
            while True:
                page = self.task_log(upid, start=len(lines))
                if not page:
                    return
                for line in page:
                    lines.append(line)
                    if on_line is not None:
                        on_line(line)
                if len(page) < TASK_LOG_PAGE:
                    return

        while True:
            status = self.task_status(upid)
            drain()
            if str(status.get("status", "")).lower() == "stopped":
                drain()
                exitstatus = status.get("exitstatus")
                if not task_succeeded(exitstatus):
                    raise PBSTaskError(
                        f"PBS task failed: {exitstatus or 'unknown status'} ({upid})",
                        upid=upid,
                        exitstatus=exitstatus,
                        log_lines=lines,
                    )
                return {**status, "log_lines": lines}
            if self._monotonic() >= deadline:
                raise PBSTaskTimeout(
                    f"PBS task still running after {int(timeout_seconds)} s ({upid})",
                    upid=upid,
                    log_lines=lines,
                )
            self._sleep(poll_interval)


def _flag(value: bool) -> int:
    return 1 if value else 0


def _seg(value: str) -> str:
    return quote(str(value), safe="")


def _require_upid(data: Any, operation: str) -> str:
    if not is_upid(data):
        raise PBSApiError(f"PBS did not return a task id (UPID) for {operation}: {data!r}")
    return data


def _describe_error(method: str, path: str, response: httpx.Response) -> str:
    prefix = f"PBS API HTTP {response.status_code} for {method} /{path.lstrip('/')}"
    try:
        body = response.json()
    except ValueError:
        text = response.text.strip()
        return f"{prefix}: {text[:500]}" if text else prefix
    parts = []
    if isinstance(body, dict):
        if body.get("message"):
            parts.append(str(body["message"]).strip())
        errors = body.get("errors")
        if isinstance(errors, dict) and errors:
            parts.append("; ".join(f"{key}: {value}" for key, value in errors.items()))
    return f"{prefix}: {' — '.join(parts)}" if parts else prefix
