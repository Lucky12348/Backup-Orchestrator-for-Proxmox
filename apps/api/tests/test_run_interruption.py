"""Phase 5f.3: stale stacked mounts (B41), a run interrupted by a removal
(B44), no false alert after an eject (B43), the verification run (B45)."""

from contextlib import ExitStack
from dataclasses import replace
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.models import ActivityEvent, BackupRunStatus, ExternalBackupRun, ExternalDisk, PbsUsbMapping
from app.services.external_backups import append_external_backup_run_log, execute_external_backup_run
from app.services.host_agent import HostAgentError
from app.services.run_log import build_entries, run_journal
from pbs_fake import FakePBS, fake_settings, make_backend, usb_disk
from test_disk_guards import TARGET, _disk, _flow, _run, _source


class FakePbsAgent:
    """Host agent answering the PBS VM guest routes (`qm guest exec` inside)."""

    def __init__(self, removed=(), live=False, guest_agent=True):
        self.removed = list(removed)
        self.live = live
        self.guest_agent = guest_agent
        self.calls = []

    def post(self, path, payload):
        self.calls.append((path, payload))
        if path == "/pbs-guest/agent":
            return SimpleNamespace(payload={"enabled": self.guest_agent, "active": self.guest_agent})
        return SimpleNamespace(payload={"guest_agent": self.guest_agent, "removed": self.removed, "live_mount": self.live, "remaining": []})


# --- B41 ------------------------------------------------------------------------------------------------
def test_dead_layers_are_removed_before_mounting_and_journalled(db, session_factory):
    fake = FakePBS()
    _source(fake)
    disk = _disk(db)
    agent = FakePbsAgent(removed=[{"source": "/dev/sdd1", "reason": "shutdown"}, {"source": "/dev/sdc1", "reason": "device_gone"}])
    with ExitStack() as stack:
        _flow(stack, fake, session_factory)
        execute_external_backup_run(_run(db, disk).id)  # creation: PBS mounts it itself
        fake.mount_status[TARGET] = "notmounted"  # pulled out, plugged back: PBO mounts it
        db.expire_all()
        run = _run(db, db.get(ExternalDisk, disk.id))
        stack.enter_context(patch("app.services.stale_mounts._host", lambda settings, client: agent))
        execute_external_backup_run(run.id)
    db.expire_all()
    assert agent.calls[0] == ("/pbs-guest/mounts/cleanup", {"vmid": 100, "path": f"/mnt/datastore/{TARGET}"})
    codes = [entry["code"] for entry in run_journal(db.get(ExternalBackupRun, run.id), None)["entries"]]
    assert codes.count("stale_mount_removed") == 2
    assert db.get(ExternalBackupRun, run.id).status == BackupRunStatus.SUCCESS


def test_never_mounts_over_a_live_filesystem(db, session_factory):
    fake = FakePBS(disks=[usb_disk(partitions=[])])
    backend = make_backend(fake, session_factory)
    _disk(db, serial_number="WD-123")
    with patch("app.services.stale_mounts._host", lambda settings, client: FakePbsAgent(live=True)):
        result = backend.post("/prepare-dedicated-pbs-datastore", {"disk": "WD-123", "datastore_name": TARGET, "confirmation": True})
        assert result.ok  # first creation: PBS mounts it itself
        fake.mount_status[TARGET] = "notmounted"  # PBS lost track, a live mount is still there
        with pytest.raises(HostAgentError) as refused:
            backend.post("/prepare-dedicated-pbs-datastore", {"disk": "WD-123", "datastore_name": TARGET, "confirmation": True})
    assert refused.value.payload["error_code"] == "live_mount_present"
    assert ("POST", f"admin/datastore/{TARGET}/mount") not in fake.mutating_calls()


def test_without_pbs_agent_the_journal_says_the_check_was_not_possible(db, session_factory):
    fake = FakePBS()
    _source(fake)
    run = _run(db, _disk(db))
    with ExitStack() as stack:
        _flow(stack, fake, session_factory)
        execute_external_backup_run(run.id)
    db.expire_all()
    codes = [entry["code"] for entry in run_journal(db.get(ExternalBackupRun, run.id), None)["entries"]]
    assert "stale_mounts_unchecked" not in codes  # created by PBS mounted: no mount of ours
    assert db.get(ExternalBackupRun, run.id).status == BackupRunStatus.SUCCESS
    fake.mount_status[TARGET] = "notmounted"
    second = _run(db, db.get(ExternalDisk, run.disk_id))
    with ExitStack() as stack:
        _flow(stack, fake, session_factory)
        execute_external_backup_run(second.id)
    db.expire_all()
    codes = [entry["code"] for entry in run_journal(db.get(ExternalBackupRun, second.id), None)["entries"]]
    assert "stale_mounts_unchecked" in codes


# --- B44 -------------------------------------------------------------------------------------------------
def _interrupt_during(stage_step: str, session_factory):
    """Simulates the watch detecting the removal while the run is at `stage_step`."""

    def interrupt(run_id):
        from app.services.passthrough_watch import handle_pulled

        with session_factory() as other:
            run = other.get(ExternalBackupRun, run_id)
            run.current_step = stage_step
            other.commit()
            host = SimpleNamespace(post=lambda path, payload: SimpleNamespace(payload={}, stdout_log=None))
            with patch("app.services.passthrough_watch.get_settings", lambda: replace(fake_settings(), pbs_mode="agent")):
                handle_pulled(other, other.get(ExternalDisk, run.disk_id), reason="mounted_on_pbs", host_client=host)

    return interrupt


@pytest.mark.parametrize(
    ("hook", "stage", "message"),
    [
        ("app.services.post_sync.cleanup", "cleanup", "before its datastore was unmounted. The copy is not guaranteed"),
        ("app.services.external_backup_execution.ExternalBackupExecutionService.execute", "sync_job", "during the copy"),
    ],
)
def test_a_removal_stops_the_run_at_once(db, session_factory, hook, stage, message):
    fake = FakePBS()
    _source(fake)
    disk = _disk(db)
    run = _run(db, disk, auto_eject_after_success=True, verify_after_backup=True)
    interrupt = _interrupt_during(stage, session_factory)
    with ExitStack() as stack:
        _flow(stack, fake, session_factory)
        stack.enter_context(patch("app.services.notifications.notify_unsafe_removal", lambda *a: None))
        if stage == "sync_job":
            def failing_execute(self, **kwargs):
                interrupt(kwargs["run_id"])
                raise RuntimeError("PBS task failed: stopped by user")

            stack.enter_context(patch(hook, failing_execute))
        else:
            stack.enter_context(patch(hook, lambda bridge, run, disk, store, log: interrupt(run.id)))
        execute_external_backup_run(run.id)
    db.expire_all()
    stored = db.get(ExternalBackupRun, run.id)
    assert (stored.status, stored.current_step) == (BackupRunStatus.FAILED, "disk_pulled")
    assert message in stored.message
    entries = run_journal(stored, None)["entries"]
    codes = [entry["code"] for entry in entries]
    pulled = codes.index("disk_pulled_during_sync" if stage == "sync_job" else "disk_pulled_after_sync")
    # Nothing normal after it: no verification, no success, no auto-eject.
    assert [code for code in codes[pulled + 1 :] if code] == []
    assert "auto_ejected" not in codes and "verify_ok" not in codes and "spin_down_unresolved" not in codes


def test_the_journal_is_one_timeline():
    """ERR lines (stderr) are no longer all at the end with an earlier time."""
    stdout = "[2026-11-17T04:00:00Z] starting A\n[2026-11-17T04:00:20Z] sync_job C"
    stderr = "[2026-11-17T04:00:10Z] failure B"
    assert [entry.text for entry in build_entries(stdout, stderr)] == ["A", "B", "C"]


def test_gc_failure_is_not_reported_as_a_retention_failure(db, session_factory):
    fake = FakePBS()
    _source(fake)
    original = fake._route

    def route(method, path, body, params):
        if method == "POST" and path.endswith("/gc"):
            return fake._task("garbage_collection", TARGET, ["TASK ERROR: device offline"], exitstatus="device offline")
        return original(method, path, body, params)

    fake._route = route
    run = _run(db, _disk(db))
    with ExitStack() as stack:
        _flow(stack, fake, session_factory)
        execute_external_backup_run(run.id)
    db.expire_all()
    codes = [entry["code"] for entry in run_journal(db.get(ExternalBackupRun, run.id), None)["entries"]]
    assert "cleanup_gc_failed" in codes and "cleanup_failed" not in codes


# --- B43 -------------------------------------------------------------------------------------------------
def test_unplugged_while_its_run_ejects_it_is_not_a_removal_without_eject(db):
    from app.schemas.agent import AgentDiskReportCreate
    from app.services.disks import ingest_agent_disk_report

    disk = _disk(db, reported_by_hostname="pve", presence_state="present", pbs_visible=False, pbs_handoff_slot=None)
    _run(db, disk, status=BackupRunStatus.RUNNING, current_step="eject_flush", auto_eject_after_success=True)
    with patch("app.services.disks.handle_disk_detected", lambda *a: None):
        ingest_agent_disk_report(db, AgentDiskReportCreate(hostname="pve", observed_at=datetime.utcnow(), disks=[], usb_devices=[]))
    db.refresh(disk)
    assert not disk.verify_recommended
    assert db.query(ActivityEvent).filter_by(action="unsafe_removal").count() == 0


def test_open_mappings_are_closed_at_the_end_of_every_run(db, session_factory):
    fake = FakePBS()
    _source(fake)
    disk = _disk(db, pbs_handoff_slot=None)
    db.add(PbsUsbMapping(vmid=100, slot="usb0", host_value="3-4", disk_id=disk.id, created_at=datetime.utcnow() - timedelta(hours=1)))
    db.commit()
    run = _run(db, disk)
    with ExitStack() as stack:
        _flow(stack, fake, session_factory)
        execute_external_backup_run(run.id)
    db.expire_all()
    assert db.query(PbsUsbMapping).one().release_reason == "run_end"


# --- B45 -------------------------------------------------------------------------------------------------
def test_a_verification_releases_the_disk_instead_of_ejecting_it(db, session_factory):
    fake = FakePBS()
    _source(fake)
    disk = _disk(db, verify_recommended=True)
    detached = []
    with ExitStack() as stack:
        _flow(stack, fake, session_factory)
        execute_external_backup_run(_run(db, disk).id)  # a backup first: creates the datastore
        db.expire_all()
        run = _run(db, db.get(ExternalDisk, disk.id), verify_only=True, verify_after_backup=True, auto_eject_after_success=False)
        stack.enter_context(patch("app.services.disk_eject._detach_usb_passthrough_from_pbs_vm", lambda d: detached.append(d.id) or "ok"))
        spin = stack.enter_context(patch("app.services.disk_eject._attempt_disk_spin_down"))
        execute_external_backup_run(run.id)
    db.expire_all()
    stored, disk = db.get(ExternalBackupRun, run.id), db.get(ExternalDisk, disk.id)
    assert stored.status == BackupRunStatus.SUCCESS and detached == [disk.id]
    spin.assert_not_called()  # no spin-down
    assert (disk.handoff_status, disk.connected, disk.pbs_handoff_slot, disk.verify_recommended) == ("detected_on_proxmox", True, None, None)
    codes = [entry["code"] for entry in run_journal(stored, disk)["entries"]]
    assert "released" in codes and "auto_ejected" not in codes


def test_verify_route_does_not_eject_by_default():
    from app.api.routes.disks import DiskVerifyRequest

    assert DiskVerifyRequest(confirmation=True).auto_eject_after_success is False


def test_interrupted_journal_accepts_only_the_interruption_line(db):
    disk = _disk(db)
    run = _run(db, disk, status=BackupRunStatus.RUNNING, interrupted="during_sync")
    append_external_backup_run_log(db, run.id, step="sync_job", message="Normal step after the removal.")
    append_external_backup_run_log(db, run.id, step="disk_pulled", message="Interrupted: the disk was pulled out during the copy. The copy is incomplete: start a new backup.", stream="stderr")
    db.refresh(run)
    assert "Normal step" not in (run.stdout_log or "") and "Interrupted" in (run.stderr_log or "")
