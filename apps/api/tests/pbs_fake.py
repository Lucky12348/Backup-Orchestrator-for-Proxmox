"""Stateful fake Proxmox Backup Server for PBS_MODE=api tests (httpx.MockTransport).

Implements the subset of the PBS 4.x REST API used by PBSClient, with the
schema's return types: UPID strings for wipedisk / disks/directory /
mount / unmount, *null* for POST /config/datastore and POST /admin/sync/{id}/run
(the sync task must then be found through /nodes/{node}/tasks).
Tasks finish after `task_polls` status polls; their side effects apply on
completion, like real worker tasks.
"""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any
from urllib.parse import unquote

import httpx

from app.core.config import Settings
from app.services.pbs_api_backend import PBSApiBackend
from app.services.pbs_client import PBSClient

GIB = 1024**3


def usb_disk(serial: str = "WD-123", *, size_gib: int = 2000, partitions: list[dict] | None = None, used: str | None = None) -> dict:
    parts = partitions if partitions is not None else [
        {"name": "sdb1", "devpath": "/dev/sdb1", "filesystem": "ntfs", "uuid": "NTFS-UUID", "used": "filesystem", "mounted": False}
    ]
    return {
        "name": "sdb",
        "devpath": "/dev/sdb",
        "serial": serial,
        "model": "Elements",
        "vendor": "WD",
        "size": size_gib * GIB,
        "disk-type": "usb",
        "gpt": True,
        "status": "unknown",
        "used": used or ("partitions" if parts else "unused"),
        "partitions": parts,
    }


class FakePBS:
    def __init__(self, *, disks: list[dict] | None = None, task_polls: int = 2) -> None:
        self.disks = disks if disks is not None else [usb_disk()]
        self.datastores: list[dict[str, Any]] = [{"name": "main", "path": "/mnt/datastore/main"}]
        self.reused: list[dict[str, Any]] = []
        self.mount_status: dict[str, str] = {"main": "nonremovable"}
        self.sync_jobs: dict[str, dict] = {}
        self.tasks: dict[str, dict] = {}
        self.calls: list[tuple[str, str, Any]] = []
        self.task_polls = task_polls
        self.sync_exitstatus = "OK"
        self.hang_unmount = False
        # Phase 5f: snapshots per datastore, retention/verify behaviour, space.
        self.snapshots: dict[str, list[dict[str, Any]]] = {"main": []}
        self.verify_exitstatus = "OK"
        self.verify_calls: list[dict[str, Any]] = []
        self.prune_calls: list[dict[str, Any]] = []
        self.gc_calls: list[str] = []
        self.status_bytes: dict[str, dict[str, int]] = {}
        self._pid = 0x1000
        self._clock = 1_700_000_000

    # --- helpers ---------------------------------------------------------------------
    def mutating_calls(self) -> list[tuple[str, str]]:
        return [(method, path) for method, path, _ in self.calls if method != "GET"]

    def _task(self, worker_type: str, worker_id: str, log: list[str], effect=None, *, exitstatus: str = "OK", hang: bool = False) -> str:
        self._pid += 1
        self._clock += 1
        escaped = worker_id.replace(":", "\\x3a")
        upid = f"UPID:localhost:{self._pid:08X}:00000001:00000000:{self._clock:08X}:{worker_type}:{escaped}:root@pam!pbo:"
        self.tasks[upid] = {
            "upid": upid,
            "worker_type": worker_type,
            "worker_id": worker_id,
            "starttime": self._clock,
            "log": log,
            "effect": effect,
            "polls_left": self.task_polls,
            "exitstatus": exitstatus,
            "hang": hang,
            "status": "running",
        }
        return upid

    def _poll(self, task: dict) -> None:
        if task["status"] == "stopped" or task["hang"]:
            return
        task["polls_left"] -= 1
        if task["polls_left"] <= 0:
            task["status"] = "stopped"
            if task["effect"] is not None and task["exitstatus"] == "OK":
                task["effect"]()

    def _disk(self, name: str) -> dict:
        return next(disk for disk in self.disks if disk["name"] == name)

    def add_snapshot(self, store: str, group: str, backup_time: int, size: int = 10 * GIB) -> dict:
        kind, ident = group.split("/", 1)
        snapshot = {"backup-type": kind, "backup-id": ident, "backup-time": backup_time, "size": size, "files": ["index.json.blob"]}
        self.snapshots.setdefault(store, []).append(snapshot)
        return snapshot

    def _sync_effect(self, job: dict) -> None:
        source = self.snapshots.get(job["remote-store"], [])
        target = self.snapshots.setdefault(job["store"], [])
        groups = {item.removeprefix("group:") for item in job.get("group-filter") or []}
        have = {(item["backup-type"], item["backup-id"], item["backup-time"]) for item in target}
        for snapshot in source:
            key = (snapshot["backup-type"], snapshot["backup-id"], snapshot["backup-time"])
            if key in have or (groups and f"{key[0]}/{key[1]}" not in groups):
                continue
            target.append(dict(snapshot))

    def _prune(self, store: str, body: dict) -> None:
        keep_last = body.get("keep-last")
        if not keep_last:
            return  # weekly/monthly not simulated
        kept: list[dict] = []
        by_group: dict[tuple, list[dict]] = {}
        for snapshot in self.snapshots.get(store, []):
            by_group.setdefault((snapshot["backup-type"], snapshot["backup-id"]), []).append(snapshot)
        for items in by_group.values():
            kept.extend(sorted(items, key=lambda item: item["backup-time"])[-int(keep_last):])
        self.snapshots[store] = kept

    def add_running_task(self, worker_type: str, worker_id: str) -> str:
        upid = self._task(worker_type, worker_id, [], hang=True)
        return upid

    # --- transport --------------------------------------------------------------------
    def handler(self, request: httpx.Request) -> httpx.Response:
        path = unquote(request.url.path.split("/api2/json/", 1)[1])
        body = json.loads(request.content) if request.content else None
        method = request.method
        self.calls.append((method, path, body))
        params = request.url.params
        try:
            data = self._route(method, path, body, params)
        except LookupError as exc:
            return httpx.Response(404, json={"message": str(exc)})
        except ValueError as exc:
            return httpx.Response(400, json={"message": str(exc)})
        return httpx.Response(200, json={"data": data})

    def _route(self, method: str, path: str, body: Any, params: Any) -> Any:
        parts = path.split("/")
        if (method, path) == ("GET", "version"):
            return {"version": "4.1.0", "release": "4.1", "repoid": "fake"}
        if (method, path) == ("GET", "nodes/localhost/disks/list"):
            return json.loads(json.dumps(self.disks))
        if (method, path) == ("PUT", "nodes/localhost/disks/wipedisk"):
            disk = self._disk(body["disk"])

            def wipe() -> None:
                disk["partitions"] = []
                disk["used"] = "unused"

            return self._task("wipedisk", body["disk"], ["wiping /dev/" + body["disk"], "TASK OK"], wipe)
        if (method, path) == ("POST", "nodes/localhost/disks/directory"):
            assert body["filesystem"] == "ext4" and body["add-datastore"] is True and body["removable-datastore"] is True
            disk = self._disk(body["disk"])
            name = body["name"]
            if not 3 <= len(name) <= 32:
                raise ValueError("name: value must have a length between 3 and 32")

            def create() -> None:
                uuid = f"UUID-{name}"
                disk["partitions"] = [{"name": "sdb1", "devpath": "/dev/sdb1", "filesystem": "ext4", "uuid": uuid, "used": "filesystem", "mounted": True}]
                disk["used"] = "partitions"
                # PBS: removable datastore → path = its name, backing-device = fs UUID.
                self.datastores.append({"name": name, "path": name, "backing-device": uuid})
                self.mount_status[name] = "mounted"

            return self._task("dircreate", name, [f"create datastore '{name}' on disk {body['disk']}", "TASK OK"], create)
        if parts[:2] == ["config", "datastore"]:
            if method == "GET" and len(parts) == 2:
                return [dict(cfg) for cfg in self.datastores]
            if method == "POST" and len(parts) == 2:
                # Re-register (reuse-datastore): schema return type null.
                if any(cfg["name"] == body["name"] for cfg in self.datastores):
                    raise ValueError(f"datastore '{body['name']}' already exists")
                self.datastores.append({key: value for key, value in body.items() if key != "reuse-datastore"})
                self.reused.append(body)
                self.mount_status[body["name"]] = "notmounted"
                return None
            if method == "DELETE" and len(parts) == 3:
                # Configuration only (no destroy-data): the data stays on the disk.
                self.datastores = [cfg for cfg in self.datastores if cfg["name"] != parts[2]]
                self.mount_status.pop(parts[2], None)
                return None
            cfg = next((c for c in self.datastores if c["name"] == parts[2]), None)
            if cfg is None:
                raise LookupError(f"datastore '{parts[2]}' does not exist")
            if method == "GET":
                return dict(cfg)
            if method == "PUT":
                cfg.update(body)
                return None
        if (method, path) == ("GET", "admin/datastore"):
            return [{"store": cfg["name"], "mount-status": self.mount_status.get(cfg["name"], "nonremovable")} for cfg in self.datastores]
        if parts[:2] == ["admin", "datastore"] and len(parts) == 4:
            store, action = parts[2], parts[3]
            if action == "status":
                return self.status_bytes.get(store, {"total": 2000 * GIB, "used": 500 * GIB, "avail": 1500 * GIB})
            if action == "snapshots" and method == "GET":
                return [dict(item) for item in self.snapshots.get(store, [])]
            if action == "prune-datastore" and method == "POST":
                assert any(key.startswith("keep-") for key in body), "prune without keep-* deletes everything"
                self.prune_calls.append({"store": store, **body})
                return self._task("prune", store, [f"prune datastore '{store}'", "TASK OK"], lambda: self._prune(store, body))
            if action == "gc" and method == "POST":
                self.gc_calls.append(store)
                return self._task("garbage_collection", store, ["starting garbage collection", "TASK OK"])
            if action == "verify" and method == "POST":
                self.verify_calls.append({"store": store, **(body or {})})
                ok = self.verify_exitstatus == "OK"
                return self._task(
                    "verify", store, ["verify datastore", "TASK OK" if ok else "TASK ERROR: verification failed"],
                    exitstatus=self.verify_exitstatus,
                )
            if action == "mount":
                return self._task("mount-device", store, ["mounting"], lambda: self.mount_status.__setitem__(store, "mounted"))
            if action == "unmount":
                return self._task(
                    "unmount-device",
                    store,
                    ["waiting for active operations", "unmounting"],
                    lambda: self.mount_status.__setitem__(store, "notmounted"),
                    hang=self.hang_unmount,
                )
        if parts[:2] == ["config", "sync"]:
            if method == "GET":
                return list(self.sync_jobs.values())
            if method == "POST":
                assert "remote" not in body, "local sync job must not have a remote"
                self.sync_jobs[body["id"]] = dict(body)
                return None
            if method == "DELETE":
                if self.sync_jobs.pop(parts[2], None) is None:
                    raise LookupError("no such job")
                return None
        if parts[:2] == ["admin", "sync"] and method == "POST":
            job = self.sync_jobs[parts[2]]
            worker_id = f"-:{job['remote-store']}:{job['store']}::{job['id']}"
            log = [
                f"Starting datastore sync job '{worker_id}'",
                "sync group vm/100 (1 of 2)",
                "percentage done: 50.00% (1/2 groups)",
                "sync group vm/101 (2 of 2)",
                "percentage done: 100.00% (2/2 groups)",
                "TASK OK" if self.sync_exitstatus == "OK" else f"TASK ERROR: {self.sync_exitstatus}",
            ]
            self._task("syncjob", worker_id, log, lambda: self._sync_effect(job), exitstatus=self.sync_exitstatus)
            return None  # schema: returns null
        if (method, path) == ("GET", "nodes/localhost/tasks"):
            items = list(self.tasks.values())
            if params.get("running") == "1":
                items = [task for task in items if task["status"] == "running"]
            if params.get("typefilter"):
                items = [task for task in items if task["worker_type"] == params["typefilter"]]
            if params.get("since"):
                items = [task for task in items if task["starttime"] >= int(params["since"])]
            return [
                {"upid": t["upid"], "worker_type": t["worker_type"], "worker_id": t["worker_id"], "starttime": t["starttime"], "status": None if t["status"] == "running" else t["exitstatus"]}
                for t in items
            ]
        if parts[:3] == ["nodes", "localhost", "tasks"] and len(parts) == 5:
            task = self.tasks[parts[3]]
            if parts[4] == "status":
                self._poll(task)
                result = {"upid": task["upid"], "status": task["status"], "type": task["worker_type"]}
                if task["status"] == "stopped":
                    result["exitstatus"] = task["exitstatus"]
                return result
            if parts[4] == "log":
                start = int(params.get("start", 0))
                limit = int(params.get("limit", 50))
                visible = task["log"] if task["status"] == "stopped" else task["log"][: max(1, len(task["log"]) // 2)]
                return [{"n": index + 1, "t": line} for index, line in enumerate(visible)][start : start + limit]
        raise LookupError(f"unexpected {method} {path}")


def fake_settings(**overrides: Any) -> Settings:
    base = Settings(
        pbs_mode="api",
        pbs_api_url="https://pbs.test:8007/api2/json",
        pbs_token_id="root@pam!pbo",
        pbs_token_secret="secret",
        pbs_datastore="main",
        pbs_task_timeout_seconds=60,
        pbs_eject_timeout_seconds=10,
    )
    return replace(base, **overrides)


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def make_backend(fake: FakePBS, session_factory=None, settings: Settings | None = None) -> PBSApiBackend:
    settings = settings or fake_settings()
    clock = FakeClock()
    client = PBSClient(settings, transport=httpx.MockTransport(fake.handler), sleep=clock.sleep, monotonic=clock.monotonic)
    return PBSApiBackend(
        settings,
        client=client,
        session_factory=session_factory,
        wall_clock=lambda: 1_700_000_000.0,
        sleep=clock.sleep,
    )
