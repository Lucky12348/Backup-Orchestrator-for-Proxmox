"""B56: cancelling a run at any step — the PBS task is stopped, the disk is
released (unmount on PBS, USB removed from the VM), the run ends "cancelled",
no verification is required, the notification says so."""

from contextlib import ExitStack
from unittest.mock import patch

import pytest
from pbs_fake import FakePBS, make_backend
from test_disk_guards import TARGET, _disk, _flow, _run, _source

from app.models import BackupRunStatus, ExternalBackupRun, ExternalDisk
from app.services import notification_catalog as catalog
from app.services import run_control
from app.services.external_backups import execute_external_backup_run
from app.services.run_log import run_journal


def _codes(run, disk=None):
    return [entry["code"] for entry in run_journal(run, disk)["entries"] if entry["code"]]


@pytest.fixture
def emitted():
    calls = []
    with patch("app.services.notifications.emit", lambda event, **kwargs: calls.append((event, kwargs))):
        yield calls


def _cancel_when(fake: FakePBS, worker_type: str, run_id: int) -> None:
    """Simulates the operator pressing Cancel as soon as PBS starts `worker_type`."""
    original = fake._task

    def task(kind, worker_id, log, effect=None, **kwargs):
        upid = original(kind, worker_id, log, effect, **kwargs)
        if kind == worker_type:
            fake.tasks[upid]["hang"] = True  # a long copy: only a stop ends it
            run_control.request(run_id)
        return upid

    fake._task = task


def test_cancel_during_the_copy_stops_pbs_and_releases_the_disk(db, session_factory, emitted):
    fake = FakePBS()
    _source(fake)
    disk = _disk(db)
    detached = []
    with ExitStack() as stack:
        _flow(stack, fake, session_factory)
        stack.enter_context(patch("app.services.disk_eject._detach_usb_passthrough_from_pbs_vm", lambda d: detached.append(d.id) or "ok"))
        execute_external_backup_run(_run(db, disk).id)  # first backup: the datastore exists
        db.expire_all()
        run = _run(db, db.get(ExternalDisk, disk.id), auto_eject_after_success=True, verify_after_backup=True)
        _cancel_when(fake, "syncjob", run.id)
        fake.calls.clear()
        execute_external_backup_run(run.id)
    db.expire_all()
    stored, disk = db.get(ExternalBackupRun, run.id), db.get(ExternalDisk, disk.id)
    assert (stored.status, stored.current_step) == (BackupRunStatus.FAILED, run_control.CANCELLED_STEP)
    # PBS: the copy task was stopped, the temporary job removed, the datastore unmounted.
    stopped = [path for method, path, _ in fake.calls if method == "DELETE" and path.startswith("nodes/localhost/tasks/")]
    assert len(stopped) == 1 and "syncjob" in stopped[0]
    assert fake.sync_jobs == {} and fake.mount_status[TARGET] == "notmounted"
    # The disk is back on the host, no verification asked, nothing else ran.
    assert detached == [disk.id]  # the first backup kept the disk (no auto-eject)
    assert (disk.handoff_status, disk.pbs_handoff_slot, disk.verify_recommended, disk.verify_required) == ("detected_on_proxmox", None, None, None)
    codes = _codes(stored, disk)
    assert codes[-2:] == ["released", "run_cancelled"] and "cancel_requested" not in codes  # requested outside the route here
    assert "verify_requested" not in codes and "auto_ejected" not in codes and "cleanup_freed" not in codes
    assert not run_control.is_requested(run.id)  # forgotten once the run ended
    event, kwargs = emitted[-1]
    assert event == "run_interrupted" and kwargs["params"]["detail"] == catalog.STOP_REASONS["cancelled"]


def test_cancel_before_the_disk_has_a_datastore(db, session_factory, emitted):
    """Cancelled before the preparation: nothing on PBS yet, the USB device is
    still removed from the VM, no verification."""
    fake = FakePBS()
    _source(fake)
    disk = _disk(db)
    detached = []
    run = _run(db, disk)
    run_control.request(run.id)
    with ExitStack() as stack:
        _flow(stack, fake, session_factory)
        stack.enter_context(patch("app.services.disk_eject._detach_usb_passthrough_from_pbs_vm", lambda d: detached.append(d.id) or "ok"))
        execute_external_backup_run(run.id)
    db.expire_all()
    stored, disk = db.get(ExternalBackupRun, run.id), db.get(ExternalDisk, disk.id)
    assert stored.current_step == run_control.CANCELLED_STEP and detached == [disk.id]
    assert not [call for call in fake.mutating_calls() if "wipedisk" in call[1] or "directory" in call[1]]
    assert disk.verify_recommended is None and disk.pbs_handoff_slot is None


def test_preparing_a_datastore_is_never_interrupted(db, session_factory, emitted):
    """A cancel during the datastore creation waits for its end (no half-made
    disk), then stops before the copy."""
    fake = FakePBS()
    _source(fake)
    disk = _disk(db)
    run = _run(db, disk)
    original = fake._task

    def task(kind, worker_id, log, effect=None, **kwargs):
        if kind == "dircreate":
            run_control.request(run.id)
        return original(kind, worker_id, log, effect, **kwargs)

    fake._task = task
    with ExitStack() as stack:
        _flow(stack, fake, session_factory)
        stack.enter_context(patch("app.services.disk_eject._detach_usb_passthrough_from_pbs_vm", lambda d: "ok"))
        execute_external_backup_run(run.id)
    db.expire_all()
    assert TARGET in {cfg["name"] for cfg in fake.datastores}  # the creation finished
    assert not [path for method, path, _ in fake.calls if method == "DELETE" and path.startswith("nodes/localhost/tasks/")]
    assert not [path for method, path, _ in fake.calls if path == "config/sync"]  # no copy started
    assert db.get(ExternalBackupRun, run.id).current_step == run_control.CANCELLED_STEP


def test_a_failed_release_after_a_copy_recommends_a_verification(db, session_factory, emitted):
    fake = FakePBS()
    _source(fake)
    disk = _disk(db)
    with ExitStack() as stack:
        _flow(stack, fake, session_factory)
        stack.enter_context(patch("app.services.disk_eject._detach_usb_passthrough_from_pbs_vm", lambda d: "ok"))
        execute_external_backup_run(_run(db, disk).id)
        db.expire_all()
        run = _run(db, db.get(ExternalDisk, disk.id))
        _cancel_when(fake, "syncjob", run.id)
        stack.enter_context(patch("app.services.disk_eject._detach_usb_passthrough_from_pbs_vm", side_effect=RuntimeError("qm set failed")))
        execute_external_backup_run(run.id)
    db.expire_all()
    disk = db.get(ExternalDisk, disk.id)
    assert disk.verify_recommended is True and disk.pbs_handoff_slot == "usb0"
    _event, kwargs = emitted[-1]
    assert kwargs["params"]["detail"] == catalog.STOP_REASONS["cancelled_not_released"]


def test_wait_for_task_ignores_a_cancel_unless_the_task_may_be_stopped(db, session_factory):
    fake = FakePBS()
    backend = make_backend(fake, session_factory)
    upid = fake._task("verify", "store", ["ok"])
    run_control.request(4242)
    try:
        with run_control.bound(4242):
            assert backend.client.wait_for_task(upid, timeout_seconds=60)["status"] == "stopped"  # not cancellable
            other = fake._task("verify", "store", [], hang=True)
            with pytest.raises(run_control.RunCancelled):
                backend.client.wait_for_task(other, timeout_seconds=60, cancellable=True)
            assert fake.tasks[other]["status"] == "stopped"
        # Another thread (no run bound) is never cancelled.
        third = fake._task("verify", "store", ["ok"])
        assert backend.client.wait_for_task(third, timeout_seconds=60, cancellable=True)["status"] == "stopped"
    finally:
        run_control.clear(4242)
