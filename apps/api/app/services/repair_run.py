"""Repair run (B47): the disk is handed to PBS and its datastore reused (never
formatted), the damaged versions are deleted ON THE DISK ONLY, copied again
from the source, verified, then the disk is released (services/repair.py
explains the plan and its limits)."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models import ExternalBackupMode, ExternalBackupRun, ExternalDisk
from app.services import disk_contents, post_sync
from app.services.external_backup_agent import AgentCommandError, get_external_backup_agent_bridge
from app.services.external_backup_execution import build_dedicated_datastore_name
from app.services.repair import describe, plan

Log = Any


def _keys(bridge: Any, store: str) -> list[str]:
    listed = bridge.pbs_command("/list-datastore-snapshots", {"datastore_name": store}, run_id=None)
    return [key for key in (disk_contents.snapshot_key(item) for item in (listed.payload or {}).get("snapshots") or []) if key]


def execute_repair(db: Session, run: ExternalBackupRun, disk: ExternalDisk, handoff_message: str, log: Log) -> str:
    bridge = get_external_backup_agent_bridge()
    source = run.datastore_name
    store = disk.pbs_datastore_name or build_dedicated_datastore_name(disk.serial_number)
    try:
        inspect = bridge.inspect_disk_on_pbs(disk)
        if not inspect.ok:
            raise RuntimeError(inspect.message)
        log(step="prepare_dedicated_datastore", message="Preparing or reusing dedicated PBS datastore.")
        prepared = bridge.prepare_dedicated_pbs_datastore(disk, store, run_id=run.id, reuse_only=True)
        if not prepared.ok:
            raise RuntimeError(prepared.message)
        store = str((prepared.payload or {}).get("datastore_name") or store)

        damaged = list(disk.damaged_snapshots or [])
        # Run creation refused lost versions without confirmation: deleting them is confirmed.
        steps = plan(damaged, _keys(bridge, store), set(_keys(bridge, source)), delete_lost=True)
        log(step="repair", message=f"Repair: {len(steps['delete'])} damaged version(s) to delete on the disk only, then copy again from `{source}`.")
        for group in sorted({key.rsplit("/", 1)[0] for key in steps["collateral"]}):
            count = sum(1 for key in steps["collateral"] if key.rsplit("/", 1)[0] == group)
            log(step="repair", message=f"Repair: {group} — {count} newer version(s) deleted on the disk to be copied again (PBS skips versions older than the newest one on the disk).")
        for key in steps["blocked"]:
            log(step="repair", message=f"Repair: {describe(key)} cannot be copied again: a newer version exists only on the disk (PBS would skip it). It is kept.", stream="stderr")
        if steps["delete"]:
            bridge.pbs_command("/delete-snapshots", {"datastore_name": store, "snapshots": steps["delete"]}, run_id=run.id)
        for key in steps["lost"]:
            log(step="repair", message=f"Repair: {describe(key)} deleted — it no longer existed anywhere else.")
        if steps["recopy"]:
            groups = sorted({key.rsplit("/", 1)[0] for key in steps["recopy"]})
            before_sync = set(_keys(bridge, store))
            log(step="run_export", message="Starting PBS datastore export to dedicated disk.")
            export = bridge.run_external_export(
                f"/mnt/datastore/{store}",
                source,
                ExternalBackupMode.DEDICATED,
                run_id=run.id,
                target_datastore_name=store,
                persist_target_datastore=True,
                group_filter=[f"group:{group}" for group in groups],
            )
            if not export.ok:
                raise AgentCommandError(export.message, stdout_log=export.stdout_log, stderr_log=export.stderr_log)
            # A group sync copies every version the source holds, not only the
            # damaged ones: the disk's retention applies (prune + GC), then
            # every version this run copied and still kept is verified.
            post_sync.cleanup(bridge, run, disk, store, log)
            copied = [key for key in _keys(bridge, store) if key not in before_sync]
            post_sync.verify(bridge, run, store, log, copied=copied, db=db, disk=disk)
            steps["recopy"] = copied
        post_sync.refresh_contents(db, bridge, run, disk, store, log)
        remaining = [item for item in damaged if item["key"] in steps["blocked"]]
        disk.damaged_snapshots = remaining or None
        if not remaining:
            from app.services import disk_guards

            disk.verify_required = None
            disk_guards.clear_verify_recommended(disk)
        db.add(disk)
        db.commit()
        log(step="verify", message=f"Repair finished: {len(steps['recopy'])} version(s) copied again and verified.")
    except Exception:
        if not run.auto_eject_after_success:
            from app.services import disk_eject

            disk_eject.release_disk_within_run(db, disk, run)  # the Release step always runs
        raise
    return f"Repair completed on `{store}`."
