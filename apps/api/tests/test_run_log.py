"""Run journal cleanup (B22, B23) on real run logs (docs/design/fixtures)."""

import re
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.models.backup_run import BackupRunStatus
from app.models.external_backup_run import ExternalBackupMode, ExternalBackupRun
from app.models.external_disk import ExternalDisk
from app.services import run_log
from app.services.external_backups import append_external_backup_run_log
from app.services.pbs_progress import parse_pbs_progress_line
from app.services.settings_service import update_settings

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "docs" / "design" / "fixtures"
ADMIN = {"username": "admin", "password": "correct horse battery"}


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _texts(entries, source=None):
    return [entry.text for entry in entries if source is None or entry.source == source]


# --- Real log: backup reusing the datastore --------------------------------------------


def test_reuse_log_progress_lines_are_not_journal_lines():
    entries = run_log.build_entries(_fixture("backup-reuse.stdout.log"))
    assert not any("percentage done" in text for text in _texts(entries))
    assert not any(text == "----" for text in _texts(entries))


def test_reuse_log_duplicates_are_collapsed():
    entries = run_log.build_entries(_fixture("backup-reuse.stdout.log"))
    pbs = _texts(entries, "pbs")
    assert pbs.count("Found 2 groups to sync (out of 2 total)") == 1
    assert pbs.count("TASK OK") == 1


def test_reuse_log_pbo_messages_are_coded_and_pbs_lines_marked():
    entries = run_log.build_entries(_fixture("backup-reuse.stdout.log"))
    by_code = {entry.code: entry for entry in entries if entry.code}
    assert by_code["handoff_usb_chosen"].params == {"method": "usb_location", "port": "3-1"}
    assert by_code["handoff_mapping"].params == {"mapping": "3-1", "serial": "PBOTESTA"}
    assert by_code["inspect_retry_failed"].level == "warn"  # a retry, not an error
    assert by_code["inspect_retry_ok"].params["device"] == "/dev/sdc"
    assert by_code["datastore_reused"].level == "ok"
    assert by_code["success_summary"].params["device"] == "/dev/sdc"
    assert by_code["auto_ejected"].step == "auto_eject"
    assert by_code["target_path_actual"].params["path"] == "/mnt/datastore/pbo-pbotesta"
    sync = [entry for entry in entries if entry.source == "pbs" and "[ct/101]: re-sync snapshot" in entry.text]
    assert len(sync) == 1 and sync[0].at == "2026-11-16T02:40:00+01:00"
    assert next(entry for entry in entries if entry.text == "TASK OK").level == "ok"
    assert all(entry.code is None for entry in entries if entry.source == "pbs")


def test_reuse_log_summary_and_source_target():
    summary = run_log.transfer_summary(_fixture("backup-reuse.stdout.log"))
    assert summary["no_new_data"] is True and summary["transferred_bytes"] == 0
    run = SimpleNamespace(datastore_name="main", target_path="/mnt/datastore/pbo-pbotesta")
    assert run_log.datastore_pair(run, SimpleNamespace(pbs_datastore_name="pbo-pbotesta")) == {
        "source_datastore": "main",
        "target_datastore": "pbo-pbotesta",
    }
    eject = SimpleNamespace(datastore_name="pbo-pbotesta", target_path="/mnt/datastore/pbo-pbotesta")
    assert run_log.datastore_pair(eject, None) == {"source_datastore": None, "target_datastore": "pbo-pbotesta"}


# --- Real log: eject ------------------------------------------------------------------------


def test_eject_log_task_ok_once_and_glued_lines_split():
    entries = run_log.build_entries(_fixture("eject.stdout.log"))
    assert _texts(entries, "pbs") == ["TASK OK"]
    codes = [entry.code for entry in entries if entry.source == "pbo"]
    assert codes == ["eject_unmount", "eject_unmount_store", "eject_detach", "spin_down", "usb_slot_removed"]
    assert next(entry for entry in entries if entry.code == "usb_slot_removed").params == {"slot": "usb0"}


# --- First backup (formatting) ------------------------------------------------------------------


def test_first_backup_wiping_and_chunkstore():
    entries = run_log.build_entries(_fixture("first-backup-format.stdout.log"))
    texts = _texts(entries)
    assert not any("Chunkstore create" in text for text in texts)
    wiping = [entry for entry in entries if entry.text == "Wiping /dev/sdc"]
    assert len(wiping) == 1  # same PBS timestamp + text: one event, kept once
    assert next(entry for entry in entries if entry.code == "sync_job_create").params == {
        "job": "pbo-sync-1", "source": "main", "target": "pbo-pbotesta",
    }


def test_first_backup_statistics_come_from_summary():
    summary = run_log.transfer_summary(_fixture("first-backup-format.stdout.log"))
    assert summary["transferred_bytes"] == int(352.682 * 1024**2)
    assert summary["rate_bytes_per_second"] == int(64.208 * 1024**2)
    assert summary["no_new_data"] is False


# --- Progress parser ------------------------------------------------------------------------------


def test_progress_parser_reads_pbs4_group_prefix_chunkstore_and_summary():
    assert parse_pbs_progress_line("2026-11-16T02:40:00+01:00: [ct/101]: re-sync snapshot 2026-11-15T18:47:31Z").current_group == "ct/101"
    assert parse_pbs_progress_line("x: Chunkstore create: 42%").chunkstore_percent == 42.0
    summary = parse_pbs_progress_line("x: Summary: sync job pulled 352.682 MiB in 9 chunks (average rate: 64.208 MiB/s)")
    assert summary.summary and summary.downloaded_bytes == int(352.682 * 1024**2)
    assert summary.current_speed == "64.208 MiB/s"
    assert parse_pbs_progress_line("x: Summary: sync job found no new data to pull").downloaded_bytes == 0


# --- Write time: what goes into the stored log ----------------------------------------------------------


def _run(db):
    disk = ExternalDisk(serial_number="PBOTESTA", display_name="QEMU", capacity_gb=100, source="agent", pbs_datastore_name="pbo-pbotesta")
    db.add(disk)
    db.commit()
    run = ExternalBackupRun(
        disk_id=disk.id, status=BackupRunStatus.RUNNING, started_at=datetime.utcnow(), created_at=datetime.utcnow(),
        target_path="/mnt/datastore/pbo-pbotesta", datastore_name="main", mode=ExternalBackupMode.DEDICATED,
    )
    db.add(run)
    db.commit()
    return run


def test_stored_log_skips_progress_and_consecutive_duplicates(db):
    run = _run(db)
    for line in (
        "2026-11-15T20:02:18+01:00: Chunkstore create: 1%",
        "2026-11-15T20:02:30+01:00: Chunkstore create: 99%",
    ):
        append_external_backup_run_log(db, run.id, step="create_datastore", message=None, line=line)
    assert run.progress_percent == 99.0 and not run.stdout_log
    append_external_backup_run_log(db, run.id, step="sync_job", message=None, line="x: Starting datastore sync job 'a'")
    assert run.progress_percent == 0.0  # new phase
    append_external_backup_run_log(db, run.id, step="sync_job", message=None, line="x: [ct/101]: percentage done: 50.00% (1/2 groups)")
    assert run.progress_percent == 50.0 and run.current_group == "ct/101" and "percentage done" not in run.stdout_log
    for _ in range(3):
        append_external_backup_run_log(db, run.id, step="sync_job", message=None, line="x: TASK OK")
    assert run.stdout_log.count("TASK OK") == 1


def test_stored_totals_cumulate_then_come_from_summary(db):
    run = _run(db)
    append_external_backup_run_log(db, run.id, step="sync_job", message=None, line="x: [ct/101]: downloaded 180.250 MiB (60.104 MiB/s)")
    append_external_backup_run_log(db, run.id, step="sync_job", message=None, line="x: [ct/102]: downloaded 172.432 MiB (70.010 MiB/s)")
    assert run.downloaded_bytes == int(180.250 * 1024**2) + int(172.432 * 1024**2)
    append_external_backup_run_log(
        db, run.id, step="sync_job", message=None, line="x: Summary: sync job pulled 352.682 MiB in 9 chunks (average rate: 64.208 MiB/s)"
    )
    assert run.downloaded_bytes == int(352.682 * 1024**2) and run.current_speed == "64.208 MiB/s"


# --- Routes ----------------------------------------------------------------------------------------


def test_log_route_and_source_target(client, session_factory):
    assert client.post("/api/v1/setup/admin", json=ADMIN).status_code == 201
    with session_factory() as db:
        run = _run(db)
        run.stdout_log = _fixture("backup-reuse.stdout.log")
        db.commit()
        run_id = run.id
    body = client.get(f"/api/v1/external-backups/runs/{run_id}/log").json()
    assert body["source_datastore"] == "main" and body["target_datastore"] == "pbo-pbotesta"
    assert body["summary"]["no_new_data"] is True
    assert any(entry["code"] == "datastore_reused" for entry in body["entries"])
    detail = client.get(f"/api/v1/external-backups/runs/{run_id}").json()
    assert (detail["source_datastore"], detail["target_datastore"], detail["cancellable"]) == ("main", "pbo-pbotesta", False)


def test_cancel_route(client, session_factory, monkeypatch):
    assert client.post("/api/v1/setup/admin", json=ADMIN).status_code == 201
    with session_factory() as db:
        run = _run(db)
        run_id = run.id
    response = client.post(f"/api/v1/external-backups/runs/{run_id}/cancel")
    assert response.status_code == 409 and response.json()["detail"]["code"] == "cancel_not_supported"

    stopped = []
    monkeypatch.setattr("app.services.pbs_client.PBSClient.stop_task", lambda self, upid: stopped.append(upid))
    with session_factory() as db:
        update_settings(db, {"PBS_MODE": "api"})
        db.get(ExternalBackupRun, run_id).pbs_task_upid = "UPID:localhost:1"
        db.commit()
    monkeypatch.setattr("app.api.routes.external_backups.get_settings", lambda: SimpleNamespace(pbs_mode="api"))
    monkeypatch.setattr("app.services.pbs_client.PBSClient.__init__", lambda self, settings=None: None)
    response = client.post(f"/api/v1/external-backups/runs/{run_id}/cancel")
    assert response.status_code == 202 and stopped == ["UPID:localhost:1"]
    with session_factory() as db:
        assert "Cancellation requested by admin." in db.get(ExternalBackupRun, run_id).stdout_log


def test_every_pbo_message_code_is_translated_by_the_ui():
    i18n = (ROOT / "apps" / "web" / "src" / "i18n.ts").read_text(encoding="utf-8")
    block = i18n.split("runLog: {", 1)
    assert len(block) == 2, "runLog translations missing"
    for code, _pattern in run_log.PBO_MESSAGES:
        assert len(re.findall(rf"\b{code}:", i18n)) >= 2, code  # en + fr


@pytest.mark.parametrize("name", ["backup-reuse.stdout.log", "eject.stdout.log", "first-backup-format.stdout.log"])
def test_every_pbo_line_of_the_fixtures_is_coded(name):
    unknown = [entry.text for entry in run_log.build_entries(_fixture(name)) if entry.source == "pbo" and not entry.code]
    assert unknown == []


@pytest.mark.parametrize("name", ["backup-reuse", "eject", "first-backup-format"])
def test_journal_json_fixtures_are_up_to_date(name):
    """docs/design/fixtures/<name>.journal.json feeds the web tests: regenerate
    it when run_log changes (same code as GET /runs/{id}/log)."""
    import json

    raw = _fixture(f"{name}.stdout.log")
    expected = {"entries": [entry.as_dict() for entry in run_log.build_entries(raw)], "summary": run_log.transfer_summary(raw)}
    assert json.loads(_fixture(f"{name}.journal.json")) == expected


@pytest.mark.parametrize("name", ["backup-reuse.stdout.log", "eject.stdout.log", "first-backup-format.stdout.log"])
def test_every_journal_line_is_timestamped_and_not_repeated(name):
    """Phase 6c: the lines of the merged end-of-run blocks get the timestamp of
    the entry they belong to, and a PBO message already journalled is not
    shown again (leftover of B22: "Disk visible on PBS" twice)."""
    entries = run_log.build_entries(_fixture(name))
    assert all(entry.at for entry in entries), [entry.text for entry in entries if not entry.at]
    pbo_texts = [entry.text for entry in entries if entry.source == "pbo"]
    assert len(pbo_texts) == len(set(pbo_texts))


def test_end_of_run_blocks_are_journalled_line_by_line(db):
    from datetime import datetime

    from app.models import BackupRunStatus, ExternalBackupMode, ExternalBackupRun, ExternalDisk
    from app.services.external_backups import _journal_block, append_external_backup_run_log

    disk = ExternalDisk(serial_number="D", display_name="D", capacity_gb=100, source="agent")
    db.add(disk)
    db.commit()
    run = ExternalBackupRun(disk_id=disk.id, status=BackupRunStatus.RUNNING, started_at=datetime(2026, 11, 16), created_at=datetime(2026, 11, 16),
                            target_path="/x", datastore_name="main", mode=ExternalBackupMode.DEDICATED)
    db.add(run)
    db.commit()
    append_external_backup_run_log(db, run.id, step="handoff_disk", message="Disk is now visible on PBS as /dev/sdb.")
    _journal_block(db, run, "prepare_datastore", "Disk is now visible on PBS as /dev/sdb.\nRequested target path: /mnt/x\n2026-11-16T10:00:00+01:00: TASK OK")
    db.refresh(run)
    lines = run.stdout_log.splitlines()
    assert len(lines) == 2 and all(line.startswith("[") for line in lines)
    assert "Requested target path: /mnt/x" in lines[1]
