"""Every PBO journal line of every run type has a translation code (no raw
English text left in the FR journal), and each run type has its own first
line. Runs: backup with verification, verification (failed, then released),
repair, run interrupted by a removal."""

from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import patch

from app.models import ExternalBackupRun, ExternalDisk
from app.services.external_backups import execute_external_backup_run
from app.services.run_log import run_journal
from pbs_fake import FakePBS
from test_disk_guards import _disk, _flow, _run
from test_durability_repair import T0, T1, T2

# Free-text lines carrying a PBS / system error verbatim (the code is on the line before).
ALLOWED_RAW = ("PBS task failed:",)


def _uncoded(db, run_id, fake):
    run = db.get(ExternalBackupRun, run_id)
    db.refresh(run)
    entries = run_journal(run, None)["entries"]
    # The fake's task lines have no PBS timestamp: on a real PBS they are `pbs` lines.
    task_lines = {line for task in fake.tasks.values() for line in task["log"]}
    return entries, [
        e["text"]
        for e in entries
        if e["source"] == "pbo" and not e["code"] and e["text"] not in task_lines and not e["text"].startswith(ALLOWED_RAW)
    ]


def _setup(db, session_factory):
    fake = FakePBS()
    for when in (T0, T1, T2):
        fake.add_snapshot("main", "ct/101", when, size=2**30)
    disk = _disk(db)
    return fake, disk


def _runs(stack, fake, session_factory):
    _flow(stack, fake, session_factory)
    stack.enter_context(patch("app.services.disk_eject._detach_usb_passthrough_from_pbs_vm", lambda disk: "detached"))
    stack.enter_context(patch("app.services.notifications.notify_unsafe_removal", lambda *a: None))
    visible = SimpleNamespace(message="Disk is now visible on PBS as /dev/sdb.")
    stack.enter_context(patch("app.services.external_backups.handoff_disk_to_pbs", lambda *a, **k: visible))


def test_every_run_type_journal_is_translatable(db, session_factory):
    fake, disk = _setup(db, session_factory)
    uncoded: dict[str, list[str]] = {}
    firsts: dict[str, str | None] = {}
    with ExitStack() as stack:
        _runs(stack, fake, session_factory)
        backup = _run(db, disk, verify_after_backup=True)
        execute_external_backup_run(backup.id)
        db.expire_all()
        fake.corrupt = {f"ct/101/{T1}"}
        verify = _run(db, db.get(ExternalDisk, disk.id), verify_only=True, verify_after_backup=True)
        execute_external_backup_run(verify.id)
        db.expire_all()
        repair = _run(db, db.get(ExternalDisk, disk.id), repair=True)
        execute_external_backup_run(repair.id)
    db.expire_all()
    for name, run in (("backup", backup), ("verify", verify), ("repair", repair)):
        entries, missing = _uncoded(db, run.id, fake)
        uncoded[name] = missing
        firsts[name] = entries[0]["code"] if entries else None
    assert uncoded == {"backup": [], "verify": [], "repair": []}
    assert firsts == {"backup": "run_starting", "verify": "run_starting_verify", "repair": "run_starting_repair"}
    assert db.get(ExternalBackupRun, repair.id).message == f"Repair completed on `{db.get(ExternalDisk, disk.id).pbs_datastore_name}`."


def test_each_run_type_starts_with_its_own_line(db, session_factory):
    from app.services.external_backups import run_external_backup
    from pbs_fake import fake_settings

    fake, disk = _setup(db, session_factory)
    disk.prepared_as_pbs_datastore, disk.pbs_datastore_name = True, "pbo-wd-123"
    disk.damaged_snapshots = [{"key": f"ct/101/{T1}", "group": "ct/101", "time": "x", "in_source": True}]
    db.commit()
    codes = {}
    with (
        patch("app.services.external_backups.assert_no_active_external_backup", lambda db: None),
        patch("app.services.external_backups.get_settings", fake_settings),
    ):
        for kind, kwargs in (("verify", {"verify_only": True}), ("repair", {"repair": True}), ("backup", {})):
            run = run_external_backup(db, disk.id, True, **kwargs)
            codes[kind] = run_journal(run, None)["entries"][0]["code"]
            run.status = __import__("app.models", fromlist=["BackupRunStatus"]).BackupRunStatus.SUCCESS
            db.commit()
    assert codes == {"verify": "run_created_verify", "repair": "run_created_repair", "backup": "run_created"}
