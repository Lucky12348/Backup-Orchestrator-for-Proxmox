"""PBS_MODE=api: the PBS agent protocol, served by the PBS REST API (PBS >= 3.3).

The orchestration code (external_backup_execution, disk_handoff, disk_eject…)
talks to "the PBS agent" through `get_pbs_agent_client()` and a handful of
agent endpoints. In API mode that function returns this backend instead of
the HTTP agent client: same method names (`post`, `get_version`…), same
request/response payload shapes, same error type (`HostAgentError`, with the
agent's `error_code` values), so the orchestration — and agent mode — stays
untouched.

Supported agent endpoints:
  /inspect-disk, /filesystem-usage                 read-only, disks/list + datastore status
  /prepare-dedicated-pbs-datastore                 guards → wipedisk → disks/directory
                                                   (ext4, add-datastore, removable-datastore)
                                                   → PBO marker comment → mount
  /run-external-export (dedicated)                 local sync job (no remote) → run → UPID log
  /eject-dedicated-pbs-datastore                   idle check → unmount → wait UPID
  /external-export-objects/status|cleanup, /cleanup-legacy-external-export-objects
Everything else (coexistence/loop-image mode, /prepare-disk, maintenance) needs
PBS_MODE=agent and fails with an explicit message.

Progress and PBS task log lines are appended to the external-backup run log
directly (no HTTP callback needed).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

from sqlalchemy import or_, select

from app.core.config import Settings, get_settings
from app.services.disk_identity import (
    canonical_serial_number,
    disk_matches_serial,
    serial_aliases,
)
from app.services.host_agent import HostAgentError, HostAgentResult
from app.services.pbs_client import (
    PBSApiError,
    PBSClient,
    PBSTaskError,
    PBSTaskTimeout,
    task_worker_tokens,
)
from app.services.pbs_guards import (
    GuardViolation,
    assert_store_idle,
    fit_datastore_name,
    match_pbs_disk,
    partition_uuids,
    pbo_marker,
    plan_dedicated_preparation,
    size_gib,
)

logger = logging.getLogger(__name__)

PBS_DATASTORE_ROOT = "/mnt/datastore"
SYNC_JOB_PREFIX = "pbo-sync-"
EJECT_NOT_FINISHED_ERROR_CODE = "eject_not_finished"  # same value as the agent / disk_eject
REUSED_MESSAGE = "Existing dedicated PBS datastore reused."  # compared by external_backup_execution
CREATED_MESSAGE = "Dedicated PBS datastore created."
EJECT_OK_MESSAGE = "External datastore unmounted safely. Disk can be detached."
API_MODE_CAPABILITIES = frozenset(
    {
        "version-endpoint",
        "inspect-disk-alias-resolution",
        "external-export-objects-status",
        "external-export-objects-cleanup",
        "dedicated-pbs-eject",
        "filesystem-usage",
        "pbs-api-mode",
    }
)
ProgressFn = Callable[..., None]


class PBSApiBackend:
    label = "PBS API"

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: PBSClient | None = None,
        session_factory: Callable[[], Any] | None = None,
        wall_clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.settings = settings or get_settings()
        self.client = client or PBSClient(self.settings)
        self._session_factory = session_factory
        self._wall_clock = wall_clock
        self._sleep = sleep
        self.base_url = self.settings.pbs_api_url
        self.timeout_seconds = self.settings.pbs_task_timeout_seconds

    # --- agent client interface -------------------------------------------------
    def get_version(self) -> dict[str, Any]:
        try:
            version = self.client.get_version()
        except (PBSApiError, RuntimeError) as exc:
            raise self._error(f"PBS API unavailable: {exc}", "GET /version") from exc
        return {
            "ok": True,
            "mode": "pbs-api",
            "agent_version": f"pbs-api ({version.get('version', '?')})",
            "pbs_version": version.get("version"),
            "capabilities": sorted(API_MODE_CAPABILITIES),
        }

    def get_health(self) -> dict[str, Any]:
        return self.get_version()

    def get(self, path: str, params: dict[str, Any] | None = None) -> HostAgentResult:
        raise self._unsupported("GET", path)

    def put(self, path: str, payload: dict[str, Any]) -> HostAgentResult:
        raise self._unsupported("PUT", path)

    def post(self, path: str, payload: dict[str, Any]) -> HostAgentResult:
        handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
            "/inspect-disk": self._inspect_disk,
            "/filesystem-usage": self._filesystem_usage,
            "/prepare-dedicated-pbs-datastore": self._prepare_dedicated,
            "/run-external-export": self._run_external_export,
            "/eject-dedicated-pbs-datastore": self._eject_dedicated,
            "/external-export-objects/status": lambda _payload: self._export_objects_status(),
            "/external-export-objects/cleanup": lambda _payload: self._cleanup_export_objects(),
            "/cleanup-legacy-external-export-objects": lambda _payload: self._cleanup_export_objects(),
        }
        handler = handlers.get(path)
        if handler is None:
            raise self._unsupported("POST", path)
        summary = f"PBS API {path}"
        try:
            result = handler(payload or {})
        except HostAgentError:
            raise
        except GuardViolation as exc:
            raise self._error(str(exc), summary, error_code=exc.code) from exc
        except (PBSTaskError, PBSTaskTimeout) as exc:
            raise self._error(str(exc), summary, stdout_log="\n".join(exc.log_lines) or None) from exc
        except (PBSApiError, RuntimeError, KeyError, ValueError) as exc:
            raise self._error(str(exc), summary) from exc
        return HostAgentResult(
            ok=bool(result.get("ok", True)),
            message=str(result.get("message") or "PBS API request completed."),
            stdout_log=result.get("stdout_log"),
            stderr_log=result.get("stderr_log"),
            command_summary=result.get("command_summary") or summary,
            execution_cwd=self.base_url,
            return_code=0 if result.get("ok", True) else 1,
            payload=result,
        )

    # --- read-only ------------------------------------------------------------------
    def _inspect_disk(self, payload: dict[str, Any]) -> dict[str, Any]:
        identifier = str(payload.get("disk") or "").strip()
        pbs_disk = self._find_disk([identifier])
        if pbs_disk is None:
            raise GuardViolation("disk_not_found", f"Disk `{identifier}` is not visible on PBS.")
        configs = self.client.list_datastores()
        filesystem_info = self._filesystem_info(pbs_disk, configs)
        return {
            "ok": True,
            "success": True,
            "disk": {
                "path": pbs_disk.get("devpath") or f"/dev/{pbs_disk.get('name')}",
                "name": pbs_disk.get("name"),
                "serial": pbs_disk.get("serial"),
                "model": pbs_disk.get("model"),
                "vendor": pbs_disk.get("vendor"),
                "size": pbs_disk.get("size"),
                "size_gb": size_gib(pbs_disk.get("size")),
                "used": pbs_disk.get("used"),
                "disk_type": pbs_disk.get("disk-type"),
            },
            "filesystem_info": filesystem_info,
            "partition_info": pbs_disk.get("partitions") or [],
            "message": "Disk inspection completed (PBS API).",
        }

    def _filesystem_usage(self, payload: dict[str, Any]) -> dict[str, Any]:
        mount_path = str(payload.get("mount_path") or "")
        store = _store_from_mount_path(mount_path)
        if store is None:
            raise GuardViolation("not_a_datastore_path", f"`{mount_path}` is not a PBS datastore mount path.")
        return {"ok": True, "mount_path": mount_path, **_usage_gb(self.client.datastore_status(store))}

    # --- preparation ------------------------------------------------------------------
    def _prepare_dedicated(self, payload: dict[str, Any]) -> dict[str, Any]:
        serial = str(payload.get("disk") or "").strip()
        target = fit_datastore_name(str(payload.get("datastore_name") or "").strip())
        progress = self._progress(payload.get("callback_run_id"))
        record = self._disk_record(serial)
        pbs_disk = self._find_disk([serial, getattr(record, "pbs_device_path", None) or ""])
        if pbs_disk is None:
            raise GuardViolation("disk_not_found", f"Disk `{serial}` is not visible on PBS.")

        plan = plan_dedicated_preparation(
            serial=serial,
            disk_record=record,
            pbs_disk=pbs_disk,
            datastore_configs=self.client.list_datastores(),
            target_name=target,
            confirmation=bool(payload.get("confirmation")),
            running_tasks=self.client.list_tasks(running=True, limit=200),
        )
        lines: list[str] = []
        commands: list[str] = []

        def on_line(step: str) -> Callable[[str], None]:
            def _record(line: str) -> None:
                lines.append(line)
                progress(step, None, line)

            return _record

        if plan.action == "format":
            for reason in plan.reasons:
                progress("destructive_format", f"Guard check: {reason}.")
            progress("destructive_format", f"Wiping `{plan.device}` on PBS (PUT /nodes/localhost/disks/wipedisk).")
            commands.append(f"PUT /nodes/localhost/disks/wipedisk disk={plan.device}")
            self.client.wait_for_task(
                self.client.wipe_disk(plan.device),
                timeout_seconds=self.settings.pbs_task_timeout_seconds,
                on_line=on_line("destructive_format"),
            )
            progress("create_datastore", f"Creating removable datastore `{target}` (ext4) on `{plan.device}`.")
            commands.append(
                f"POST /nodes/localhost/disks/directory disk={plan.device} name={target} filesystem=ext4 "
                "add-datastore=1 removable-datastore=1"
            )
            self.client.wait_for_task(
                self.client.create_directory_datastore(disk=plan.device, name=target),
                timeout_seconds=self.settings.pbs_task_timeout_seconds,
                on_line=on_line("create_datastore"),
            )
            try:
                self.client.update_datastore(target, comment=pbo_marker(serial))
            except PBSApiError as exc:
                raise PBSApiError(
                    f"Datastore `{target}` was created but the PBO marker could not be written ({exc}). "
                    f"Set its comment to `{pbo_marker(serial)}` in PBS before the next run."
                ) from exc
            commands.append(f"PUT /config/datastore/{target} comment={pbo_marker(serial)}")
            message = CREATED_MESSAGE
        else:
            progress("prepare_dedicated_datastore", f"PBO-marked datastore `{plan.datastore_name}` found on this disk.")
            message = REUSED_MESSAGE

        self._ensure_mounted(plan.datastore_name, progress, lines)
        mount_path = f"{PBS_DATASTORE_ROOT}/{plan.datastore_name}"
        return {
            "ok": True,
            "success": True,
            "device_path": plan.devpath or f"/dev/{plan.device}",
            "mount_path": mount_path,
            "target_path": mount_path,
            "actual_target_path": mount_path,
            "filesystem_type": "ext4",
            "datastore_name": plan.datastore_name,
            "command_summary": "\n".join(commands) or "PBS API (reuse, no formatting)",
            "stdout_log": "\n".join(lines) or None,
            "stderr_log": None,
            "message": message,
        }

    def _ensure_mounted(self, store: str, progress: ProgressFn, lines: list[str]) -> None:
        state = self._mount_status(store)
        if state == "mounted":
            return
        if state != "notmounted":
            raise GuardViolation(
                "datastore_not_removable",
                f"Datastore `{store}` is not a removable datastore (mount-status: {state}).",
            )
        progress("mount_datastore", f"Mounting removable datastore `{store}`.")
        self.client.wait_for_task(
            self.client.mount_datastore(store),
            timeout_seconds=self.settings.pbs_task_timeout_seconds,
            on_line=lambda line: (lines.append(line), progress("mount_datastore", None, line)),
        )
        if self._mount_status(store) != "mounted":
            raise PBSApiError(f"Datastore `{store}` is still not mounted after the mount task.")

    # --- export -----------------------------------------------------------------------------
    def _run_external_export(self, payload: dict[str, Any]) -> dict[str, Any]:
        if str(payload.get("mode") or "") != "dedicated":
            raise GuardViolation(
                "mode_unsupported",
                "Coexistence (loop image) exports are not available with PBS_MODE=api; use PBS_MODE=agent.",
            )
        source = str(payload.get("datastore_name") or "").strip()
        target = fit_datastore_name(str(payload.get("target_datastore_name") or "").strip())
        if not source or not target:
            raise GuardViolation("datastore_missing", "Source (PBS_DATASTORE) and target datastore names are required.")
        run_id = payload.get("callback_run_id")
        progress = self._progress(run_id)
        lines: list[str] = []

        self._ensure_mounted(target, progress, lines)
        if source not in {str(cfg.get("name")) for cfg in self.client.list_datastores()}:
            raise GuardViolation("source_missing", f"Source datastore `{source}` does not exist on PBS.")
        assert_store_idle(target, self.client.list_tasks(running=True, limit=200))
        self._cleanup_export_objects()

        job_id = f"{SYNC_JOB_PREFIX}{run_id}" if run_id is not None else f"{SYNC_JOB_PREFIX}{int(self._wall_clock())}"
        owner = self.settings.pbs_token_id.split("!", 1)[0] if "!" in self.settings.pbs_token_id else None
        progress("sync_job", f"Creating local PBS sync job `{job_id}`: `{source}` → `{target}` (no remote).")
        group_filter = [str(item) for item in payload.get("group_filter") or [] if str(item).startswith("group:")]
        if group_filter:
            progress("sync_job", f"Copying only: {', '.join(item.removeprefix('group:') for item in group_filter)}.")
        self.client.create_local_sync_job(
            job_id=job_id,
            store=target,
            remote_store=source,
            owner=owner,
            remove_vanished=False,
            comment=f"PBO export run {run_id}" if run_id is not None else "PBO export",
            group_filter=group_filter or None,
        )
        upid: str | None = None
        try:
            since = int(self._wall_clock()) - 5
            upid = self.client.run_sync_job(job_id) or self.client.find_task_upid(
                worker_type="syncjob",
                worker_id_contains=job_id,
                since=since,
            )
            if not upid:
                raise PBSApiError(f"Sync job `{job_id}` was started but its PBS task could not be found.")
            progress("sync_job", f"PBS sync task started: {upid}")
            self.client.wait_for_task(
                upid,
                timeout_seconds=self.settings.pbs_task_timeout_seconds,
                on_line=lambda line: (lines.append(line), progress("sync_job", None, line)),
            )
        finally:
            try:
                self.client.delete_sync_job(job_id)
            except PBSApiError as exc:  # stale job is cleaned up on the next run
                logger.warning("Could not delete temporary sync job %s: %s", job_id, exc)

        return {
            "ok": True,
            "success": True,
            "message": "PBS local sync job completed.",
            "pbs_sync_job_id": job_id,
            "pbs_task_upid": upid,
            "target_path": f"{PBS_DATASTORE_ROOT}/{target}",
            "datastore_name": target,
            "stdout_log": "\n".join(lines) or None,
            "command_summary": f"POST /config/sync id={job_id} store={target} remote-store={source}\nPOST /admin/sync/{job_id}/run",
        }

    # --- eject --------------------------------------------------------------------------------
    def _eject_dedicated(self, payload: dict[str, Any]) -> dict[str, Any]:
        store = fit_datastore_name(str(payload.get("datastore_name") or "").strip())
        progress = self._progress(payload.get("callback_run_id"))
        lines: list[str] = []
        state = self._mount_status(store)
        if state is None:
            raise GuardViolation("datastore_missing", f"Datastore `{store}` does not exist on PBS.")
        if state == "nonremovable":
            raise GuardViolation(
                "legacy_datastore",
                f"Datastore `{store}` was prepared by the PBS agent (fstab mount, not a removable datastore). "
                "Eject it with PBS_MODE=agent, or migrate it (docs/PBS_API.md).",
            )
        assert_store_idle(store, self.client.list_tasks(running=True, limit=200))

        if state == "mounted":
            progress("eject", f"Unmounting removable datastore `{store}` (POST /admin/datastore/{store}/unmount).")
            upid = self.client.unmount_datastore(store)
            try:
                self.client.wait_for_task(
                    upid,
                    timeout_seconds=self.settings.pbs_eject_timeout_seconds,
                    on_line=lambda line: (lines.append(line), progress("eject_flush", None, line)),
                )
            except PBSTaskTimeout as exc:
                raise self._error(
                    f"PBS unmount of `{store}` still running after {int(self.settings.pbs_eject_timeout_seconds)} s.",
                    "PBS API /eject-dedicated-pbs-datastore",
                    error_code=EJECT_NOT_FINISHED_ERROR_CODE,
                    stdout_log="\n".join(exc.log_lines) or None,
                ) from exc
            if not self._wait_unmounted(store):
                raise self._error(
                    f"PBS reported the unmount task finished but `{store}` is still mounted.",
                    "PBS API /eject-dedicated-pbs-datastore",
                    error_code=EJECT_NOT_FINISHED_ERROR_CODE,
                    stdout_log="\n".join(lines) or None,
                )
        else:
            progress("eject", f"Datastore `{store}` is already unmounted.")

        return {
            "ok": True,
            "success": True,
            "message": EJECT_OK_MESSAGE,
            "serial": payload.get("serial"),
            "datastore_name": store,
            "mount_path": f"{PBS_DATASTORE_ROOT}/{store}",
            "stdout_log": "\n".join(lines) or None,
            "command_summary": f"POST /admin/datastore/{store}/unmount",
        }

    def _wait_unmounted(self, store: str, attempts: int = 5, delay_seconds: float = 2.0) -> bool:
        for attempt in range(attempts):
            if self._mount_status(store) == "notmounted":
                return True
            if attempt < attempts - 1:
                self._sleep(delay_seconds)
        return False

    # --- temporary objects --------------------------------------------------------------------
    def _export_objects_status(self) -> dict[str, Any]:
        jobs = [str(job.get("id")) for job in self.client.list_sync_jobs() if str(job.get("id", "")).startswith(SYNC_JOB_PREFIX)]
        running_ids = set()
        for task in self.client.list_tasks(running=True, typefilter="syncjob", limit=200):
            running_ids.update(job for job in jobs if job in task_worker_tokens(task))
        items = [{"kind": "sync-job", "name": job, "status": "active" if job in running_ids else "stale"} for job in jobs]
        active_run_id = None
        for job in running_ids:
            suffix = job[len(SYNC_JOB_PREFIX):]
            if suffix.isdigit():
                active_run_id = int(suffix)
        return {
            "ok": True,
            "active": bool(running_ids),
            "active_run_id": active_run_id,
            "items": items,
            "message": f"{len(items)} PBO sync job(s), {len(running_ids)} running.",
        }

    def _cleanup_export_objects(self) -> dict[str, Any]:
        status = self._export_objects_status()
        if status["active"]:
            return {**status, "ok": False, "message": "Refusing cleanup because a PBO PBS sync task is running."}
        removed = []
        for item in status["items"]:
            self.client.delete_sync_job(item["name"])
            removed.append(item["name"])
        return {"ok": True, "active": False, "items": status["items"], "removed": removed, "message": f"Removed {len(removed)} stale PBO sync job(s)."}

    # --- helpers ---------------------------------------------------------------------------------
    def _find_disk(self, identifiers: list[str]) -> dict[str, Any] | None:
        candidates: list[str] = []
        for value in identifiers:
            if not value:
                continue
            for item in (value, canonical_serial_number(value), *serial_aliases(value)):
                if item and item not in candidates:
                    candidates.append(item)
        return match_pbs_disk(self.client.list_disks(include_partitions=True), candidates)

    def _filesystem_info(self, pbs_disk: dict[str, Any], configs: list[dict[str, Any]]) -> dict[str, Any]:
        partitions = [part for part in pbs_disk.get("partitions") or [] if isinstance(part, dict)]
        uuids = partition_uuids(pbs_disk)
        bound = next((cfg for cfg in configs if str(cfg.get("backing-device") or "") in uuids), None)
        part = next((p for p in partitions if bound and p.get("uuid") == bound.get("backing-device")), None)
        part = part or next((p for p in partitions if p.get("filesystem")), None)
        info: dict[str, Any] = {
            "device_path": (part or {}).get("devpath"),
            "filesystem_type": (part or {}).get("filesystem"),
            "uuid": (part or {}).get("uuid"),
            "mount_path": None,
            "filesystem_total_gb": None,
            "filesystem_used_gb": None,
            "filesystem_free_gb": None,
        }
        if bound is not None and self._mount_status(str(bound.get("name"))) == "mounted":
            store = str(bound.get("name"))
            info["mount_path"] = f"{PBS_DATASTORE_ROOT}/{store}"
            info["datastore_name"] = store
            info.update(_usage_gb(self.client.datastore_status(store)))
        return info

    def _mount_status(self, store: str) -> str | None:
        for entry in self.client.list_admin_datastores():
            if entry.get("store") == store:
                return str(entry.get("mount-status") or "nonremovable")
        return None

    def _sessions(self):  # noqa: ANN202
        if self._session_factory is not None:
            return self._session_factory()
        from app.db.session import SessionLocal

        return SessionLocal()

    def _disk_record(self, serial: str) -> SimpleNamespace | None:
        from app.models.external_disk import ExternalDisk

        with self._sessions() as db:
            candidates = db.scalars(
                select(ExternalDisk).where(
                    or_(ExternalDisk.serial_number == serial, ExternalDisk.canonical_serial_number == serial)
                )
            ).all() or [disk for disk in db.scalars(select(ExternalDisk)).all() if disk_matches_serial(disk, serial)]
            if not candidates:
                return None
            disk = candidates[0]
            return SimpleNamespace(
                id=disk.id,
                serial_number=disk.serial_number,
                trusted=bool(disk.trusted),
                pbs_device_path=disk.pbs_device_path,
            )

    def _progress(self, run_id: Any) -> ProgressFn:
        if not isinstance(run_id, int):
            return lambda step, message=None, line=None: None

        def report(step: str, message: str | None = None, line: str | None = None) -> None:
            from app.services.external_backups import append_external_backup_run_log

            try:
                with self._sessions() as db:
                    append_external_backup_run_log(db, run_id, step=step, message=message, line=line)
            except Exception as exc:  # logging must never break the PBS operation
                logger.warning("Could not append to run %s log: %s", run_id, exc)

        return report

    def _error(
        self,
        message: str,
        command_summary: str,
        *,
        error_code: str | None = None,
        stdout_log: str | None = None,
    ) -> HostAgentError:
        payload: dict[str, Any] = {"ok": False, "success": False, "message": message}
        if error_code:
            payload["error_code"] = error_code
        return HostAgentError(
            message,
            stdout_log=stdout_log,
            stderr_log=message,
            command_summary=command_summary,
            execution_cwd=self.base_url,
            return_code=1,
            status_code=500,
            payload=payload,
        )

    def _unsupported(self, method: str, path: str) -> HostAgentError:
        return self._error(
            f"`{method} {path}` is not available with PBS_MODE=api. Coexistence/legacy flows and PBS agent "
            "maintenance need PBS_MODE=agent.",
            f"{method} {path}",
            error_code="unsupported_in_api_mode",
        )


def _store_from_mount_path(mount_path: str) -> str | None:
    prefix = f"{PBS_DATASTORE_ROOT}/"
    if not mount_path.startswith(prefix):
        return None
    store = mount_path[len(prefix):].strip("/").split("/", 1)[0]
    return store or None


def _usage_gb(status: dict[str, Any]) -> dict[str, int | None]:
    def gib(key: str) -> int | None:
        value = status.get(key)
        return size_gib(value) if isinstance(value, int) else None

    return {
        "filesystem_total_gb": gib("total"),
        "filesystem_used_gb": gib("used"),
        "filesystem_free_gb": gib("avail"),
    }
