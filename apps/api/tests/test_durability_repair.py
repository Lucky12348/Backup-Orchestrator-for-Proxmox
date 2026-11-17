"""Phase 5f.4: durable writes (B46) and damaged versions (B47), against the
simulated PBS. Real test: run 30 pulled out during the GC after a sync in
TASK OK, run 31's verify found 3 versions with a 0-byte index.json.blob."""

from contextlib import ExitStack
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from app.models import BackupRunStatus, ExternalBackupRun, ExternalDisk
from app.services.external_backups import execute_external_backup_run, run_external_backup
from app.services.host_agent import HostAgentError
from app.services.repair import parse_failed_snapshots, plan
from app.services.run_log import run_journal
from pbs_fake import FakePBS, make_backend, usb_disk
from test_disk_guards import TARGET, _disk, _flow, _run

T0 = int(datetime(2026, 11, 17, 3, 7, 14, tzinfo=timezone.utc).timestamp())
T1 = int(datetime(2026, 11, 17, 4, 1, 25, tzinfo=timezone.utc).timestamp())
T2 = int(datetime(2026, 11, 17, 4, 5, 47, tzinfo=timezone.utc).timestamp())


def _codes(db, run_id):
    run = db.get(ExternalBackupRun, run_id)
    db.refresh(run)
    return [entry["code"] for entry in run_journal(run, None)["entries"] if entry["code"]]


def _with_runs(stack, fake, session_factory):
    _flow(stack, fake, session_factory)
    stack.enter_context(patch("app.services.disk_eject._detach_usb_passthrough_from_pbs_vm", lambda disk: "detached"))
    stack.enter_context(patch("app.services.notifications.notify_unsafe_removal", lambda *a: None))


def _backed_up(db, session_factory, fake):
    """A disk with its datastore and the ct/101 versions T0, T1, T2 copied."""
    for when in (T0, T1, T2):
        fake.add_snapshot("main", "ct/101", when, size=2**30)
    disk = _disk(db)
    with ExitStack() as stack:
        _with_runs(stack, fake, session_factory)
        execute_external_backup_run(_run(db, disk).id)
    db.expire_all()
    return db.get(ExternalDisk, disk.id)


# --- parsing the real run 31 ---------------------------------------------------------------------------
RUN_31 = """2026-11-17T05:32:10+01:00: verify datastore pbo-pbotesta
2026-11-17T05:32:10+01:00: verify pbo-pbotesta:ct/101/2026-11-17T04:05:47Z - manifest load error: unable to load blob '"/mnt/datastore/pbo-pbotesta/ct/101/2026-11-17T04:05:47Z/index.json.blob"' - blob too small (0 bytes).
2026-11-17T05:32:10+01:00: verify pbo-pbotesta:ct/101/2026-11-17T04:01:25Z - manifest load error: unable to load blob - blob too small (0 bytes).
2026-11-17T05:32:10+01:00: verify pbo-pbotesta:ct/102/2026-11-17T04:01:29Z - manifest load error: unable to load blob - blob too small (0 bytes).
2026-11-17T05:32:13+01:00: Failed to verify the following snapshots/groups:
2026-11-17T05:32:13+01:00: ct/101/2026-11-17T04:05:47Z
2026-11-17T05:32:13+01:00: ct/101/2026-11-17T04:01:25Z
2026-11-17T05:32:13+01:00: ct/102/2026-11-17T04:01:29Z
2026-11-17T05:32:13+01:00: TASK ERROR: verification failed - please check the log for details""".splitlines()


def test_the_damaged_versions_of_run_31_are_read_from_the_pbs_log():
    failed = parse_failed_snapshots(RUN_31)
    assert [(item["group"], item["time"]) for item in failed] == [
        ("ct/101", "2026-11-17T04:05:47Z"),
        ("ct/101", "2026-11-17T04:01:25Z"),
        ("ct/102", "2026-11-17T04:01:29Z"),
    ]
    assert failed[0]["key"] == f"ct/101/{T2}" and "blob too small (0 bytes)" in failed[0]["reason"]


# --- B46: durable writes -------------------------------------------------------------------------------------
def test_new_and_existing_datastores_are_set_to_sync_level_file(db, session_factory):
    fake = FakePBS(disks=[usb_disk(partitions=[])])
    backend = make_backend(fake, session_factory)
    _disk(db, serial_number="WD-123")
    assert backend.post("/prepare-dedicated-pbs-datastore", {"disk": "WD-123", "datastore_name": TARGET, "confirmation": True}).ok
    config = next(cfg for cfg in fake.datastores if cfg["name"] == TARGET)
    assert config["tuning"] == "sync-level=file"
    # An existing datastore (PBS default, other keys kept) is fixed at its next run.
    config["tuning"] = "sync-level=filesystem,chunk-order=none"
    assert backend.post("/prepare-dedicated-pbs-datastore", {"disk": "WD-123", "datastore_name": TARGET, "confirmation": True}).ok
    assert config["tuning"] == "sync-level=file,chunk-order=none"


def test_pulled_out_before_the_unmount_requires_a_verification_first(db, session_factory):
    """Run 30: pulled during the cleanup → never "copy finished"."""
    from test_run_interruption import _interrupt_during

    fake = FakePBS()
    disk = _backed_up(db, session_factory, fake)
    run = _run(db, disk)
    interrupt = _interrupt_during("cleanup", session_factory)
    with ExitStack() as stack:
        _with_runs(stack, fake, session_factory)
        stack.enter_context(patch("app.services.post_sync.cleanup", lambda bridge, run, disk, store, log: interrupt(run.id)))
        execute_external_backup_run(run.id)
    db.expire_all()
    stored, disk = db.get(ExternalBackupRun, run.id), db.get(ExternalDisk, disk.id)
    assert "copy is not guaranteed" in stored.message and "finished" not in stored.message
    assert disk.verify_required is True
    from app.services.disk_guards import disk_alerts

    assert [a["code"] for a in disk_alerts(db) if a["disk_id"] == disk.id][0] == "verify_required"

    # The next backup starts with a full verification, before any copy.
    disk.handoff_status, disk.connected = "attached_to_pbs", True
    db.commit()
    fake.verify_calls.clear()
    next_run = _run(db, disk)
    with ExitStack() as stack:
        _with_runs(stack, fake, session_factory)
        execute_external_backup_run(next_run.id)
    db.expire_all()
    assert fake.verify_calls[0] == {"store": TARGET, "ignore-verified": False}
    codes = _codes(db, next_run.id)
    assert codes.index("verify_required_first") < codes.index("sync_job_create")
    assert db.get(ExternalDisk, disk.id).verify_required is None


def test_a_failed_first_verification_stops_before_copying(db, session_factory):
    fake = FakePBS()
    disk = _backed_up(db, session_factory, fake)
    disk.verify_required = True
    db.commit()
    fake.corrupt = {f"ct/101/{T1}"}
    run = _run(db, disk)
    jobs_before = len(fake.tasks)
    with ExitStack() as stack:
        _with_runs(stack, fake, session_factory)
        execute_external_backup_run(run.id)
    db.expire_all()
    stored = db.get(ExternalBackupRun, run.id)
    assert (stored.status, stored.current_step) == (BackupRunStatus.FAILED, "verify_failed")
    assert not [t for t in list(fake.tasks.values())[jobs_before:] if t["worker_type"] == "syncjob"]  # nothing copied
    assert [item["key"] for item in db.get(ExternalDisk, disk.id).damaged_snapshots] == [f"ct/101/{T1}"]


# --- B47: failed verification ---------------------------------------------------------------------------------
def test_a_failed_verification_still_releases_the_disk_and_explains(db, session_factory):
    fake = FakePBS()
    disk = _backed_up(db, session_factory, fake)
    fake.corrupt = {f"ct/101/{T1}", f"ct/101/{T2}"}
    fake.snapshots["main"] = [item for item in fake.snapshots["main"] if item["backup-time"] != T1]  # T1 pruned on the source
    run = _run(db, disk, verify_only=True, verify_after_backup=True)
    with ExitStack() as stack:
        _with_runs(stack, fake, session_factory)
        execute_external_backup_run(run.id)
    db.expire_all()
    stored, disk = db.get(ExternalBackupRun, run.id), db.get(ExternalDisk, disk.id)
    assert stored.status == BackupRunStatus.FAILED
    codes = _codes(db, run.id)
    assert "released" in codes  # the Release step ran after the error
    assert "verify_damaged_lost" in codes  # T1 no longer in the source
    assert (disk.handoff_status, disk.connected) == ("detected_on_proxmox", True)
    damaged = {item["key"]: item["in_source"] for item in disk.damaged_snapshots}
    assert damaged == {f"ct/101/{T1}": False, f"ct/101/{T2}": True}


def test_repair_deletes_on_the_disk_only_and_handles_older_than_newest(db, session_factory):
    fake = FakePBS()
    disk = _backed_up(db, session_factory, fake)
    fake.corrupt = {f"ct/101/{T0}", f"ct/101/{T1}"}  # T2 intact and newer: PBS would skip T0/T1
    disk.damaged_snapshots = [
        {"key": f"ct/101/{T0}", "group": "ct/101", "time": "2026-11-17T03:07:14Z", "in_source": True},
        {"key": f"ct/101/{T1}", "group": "ct/101", "time": "2026-11-17T04:01:25Z", "in_source": True},
    ]
    disk.verify_required = True
    db.commit()
    run = _run(db, disk, repair=True)
    with ExitStack() as stack:
        _with_runs(stack, fake, session_factory)
        execute_external_backup_run(run.id)
    db.expire_all()
    stored, disk = db.get(ExternalBackupRun, run.id), db.get(ExternalDisk, disk.id)
    assert stored.status == BackupRunStatus.SUCCESS, stored.message
    assert sorted(key for store, key in fake.deleted) == sorted([f"ct/101/{T0}", f"ct/101/{T1}", f"ct/101/{T2}"])
    assert {store for store, _ in fake.deleted} == {TARGET}  # never the source
    assert sorted(item["backup-time"] for item in fake.snapshots[TARGET]) == [T0, T1, T2]  # all copied again
    assert len(fake.snapshots["main"]) == 3
    assert (disk.damaged_snapshots, disk.verify_required) == (None, None)
    codes = _codes(db, run.id)
    assert "repair_collateral" in codes and "repair_done" in codes and "released" in codes


def test_repair_plan_keeps_what_cannot_come_back():
    disk_keys = [f"ct/101/{T0}", f"ct/101/{T2}"]
    damaged = [{"key": f"ct/101/{T0}", "group": "ct/101"}]
    # The newer T2 exists only on the disk: deleting it would lose it → T0 is blocked.
    blocked = plan(damaged, disk_keys, {f"ct/101/{T0}"}, delete_lost=True)
    assert (blocked["blocked"], blocked["delete"]) == ([f"ct/101/{T0}"], [])
    # Damaged and gone from the source: deleted only when confirmed.
    lost = plan([{"key": f"ct/101/{T2}", "group": "ct/101"}], disk_keys, set(), delete_lost=False)
    assert (lost["lost"], lost["delete"]) == ([f"ct/101/{T2}"], [])


def test_a_version_only_left_damaged_needs_confirmation(db, session_factory):
    fake = FakePBS()
    disk = _backed_up(db, session_factory, fake)
    disk.damaged_snapshots = [{"key": f"ct/101/{T1}", "group": "ct/101", "time": "2026-11-17T04:01:25Z", "in_source": False}]
    db.commit()
    from pbs_fake import fake_settings

    with (
        patch("app.services.external_backups.assert_no_active_external_backup", lambda db: None),
        patch("app.services.external_backups.get_settings", fake_settings),
    ):
        with pytest.raises(HTTPException) as refused:
            run_external_backup(db, disk.id, True, repair=True)
        assert refused.value.detail["code"] == "repair_confirm_lost"
        assert refused.value.detail["reasons"][0]["lost"] == ["ct/101 2026-11-17T04:01:25Z"]
        run = run_external_backup(db, disk.id, True, repair=True, confirm_lost=True)
    assert run.repair is True


def test_deleting_on_the_source_datastore_is_refused():
    backend = make_backend(FakePBS())
    with pytest.raises(HostAgentError) as refused:
        backend.post("/delete-snapshots", {"datastore_name": "main", "snapshots": ["ct/101/1"]})
    assert refused.value.payload["error_code"] == "delete_refused"


def test_repair_applies_the_disk_retention_then_verifies_what_it_copied(db, session_factory):
    """5f.5 (real run 36): a group resync copies every version of the source;
    the disk's retention then applies, and every version the run copied and
    kept is verified, not only the damaged one."""
    from app.services import post_sync

    T3 = T2 + 3600
    fake = FakePBS()
    fake.add_snapshot("main", "ct/101", T3, size=2**30)
    disk = _disk(db)
    disk.retention = {"keep_last": 2}
    db.commit()
    for when in (T0, T1, T2):
        fake.add_snapshot("main", "ct/101", when, size=2**30)
    with ExitStack() as stack:
        _with_runs(stack, fake, session_factory)
        execute_external_backup_run(_run(db, disk).id)
    db.expire_all()
    disk = db.get(ExternalDisk, disk.id)
    assert sorted(item["backup-time"] for item in fake.snapshots[TARGET]) == [T2, T3]
    fake.corrupt = {f"ct/101/{T2}"}
    disk.damaged_snapshots = [{"key": f"ct/101/{T2}", "group": "ct/101", "time": "x", "in_source": True}]
    db.commit()
    verified: list = []
    original = post_sync.verify

    def spy(*args, **kwargs):
        verified.append(sorted(kwargs.get("copied") or []))
        return original(*args, **kwargs)

    run = _run(db, disk, repair=True)
    with ExitStack() as stack:
        _with_runs(stack, fake, session_factory)
        stack.enter_context(patch.object(post_sync, "verify", spy))
        fake.corrupt = set()  # the re-copied versions are sound
        execute_external_backup_run(run.id)
    db.expire_all()
    stored = db.get(ExternalBackupRun, run.id)
    assert stored.status == BackupRunStatus.SUCCESS, stored.message
    assert sorted(item["backup-time"] for item in fake.snapshots[TARGET]) == [T2, T3]  # retention, not 4 versions
    assert verified == [sorted([f"ct/101/{T2}", f"ct/101/{T3}"])]
    codes = _codes(db, run.id)
    assert codes.index("repair_start") < codes.index("cleanup_pending") < codes.index("repair_done")
