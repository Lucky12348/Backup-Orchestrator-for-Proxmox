"""Phase 6c: datastores managed by the app — source missing (a), rotation disk
configuration deleted (b, re-created with reuse-datastore, NEVER wiped),
name taken by a datastore PBO did not create (c), source choice."""

from datetime import datetime

import pytest

from app.models import ExternalDisk, ScheduledBackupEvent, ScheduledBackupRecurrenceType, ScheduledBackupStartMode
from app.services import backup_readiness, datastore_health
from app.services.external_backup_execution import build_dedicated_datastore_name
from app.services.host_agent import HostAgentError
from app.services.pbs_guards import pbo_marker
from app.services.settings_service import update_settings
from pbs_fake import FakePBS, make_backend, usb_disk

SERIAL = "WD-123"
TARGET = build_dedicated_datastore_name(SERIAL)
NOW = datetime(2026, 11, 16, 12, 0)
ADMIN = {"username": "admin", "password": "correct horse battery"}


@pytest.fixture
def api_mode(db, secret_key):
    update_settings(db, {"PBS_MODE": "api", "PBS_API_URL": "https://pbs:8007", "PBS_DATASTORE": "main"})
    return db


def _disk(db, **overrides) -> ExternalDisk:
    values = dict(
        serial_number=SERIAL, display_name="WD", capacity_gb=2000, connected=True, dedicated_backup_disk=True, trusted=True,
        source="agent", active=True, pbs_visible=True, pbs_handoff_slot="usb0", pbs_device_path="/dev/sdb",
    )
    values.update(overrides)
    disk = ExternalDisk(**values)
    db.add(disk)
    db.commit()
    return disk


def _event(db, datastore="main"):
    event = ScheduledBackupEvent(
        title="Rotation", enabled=True, disk_serial=SERIAL, datastore=datastore, recurrence_type=ScheduledBackupRecurrenceType.WEEKLY,
        timezone="UTC", window_starts_at=NOW, window_duration_minutes=60, notify_before_minutes=0,
        start_mode=ScheduledBackupStartMode.MANUAL_CONFIRMATION, created_at=NOW, updated_at=NOW,
    )
    db.add(event)
    db.commit()
    return event


def _wiped(fake: FakePBS) -> list:
    return [call for call in fake.mutating_calls() if call[1] in {"nodes/localhost/disks/wipedisk", "nodes/localhost/disks/directory"}]


# --- a) source datastore deleted -------------------------------------------------------------


def test_source_missing_blocks_backups_and_plannings(api_mode):
    db = api_mode
    disk = _disk(db)
    event = _event(db)
    fake = FakePBS()
    fake.datastores = [{"name": "other", "path": "/mnt/datastore/other"}]
    health = datastore_health.check(db, client=make_backend(fake).client)
    assert health["source"] == {"name": "main", "state": "missing"}
    assert health["blocked_events"] == [event.id]
    codes = [reason["code"] for reason in backup_readiness.local_disk_reasons(db, disk)]
    assert "source_datastore_missing" in codes


def test_choose_another_datastore_updates_setting_and_plannings(api_mode):
    db = api_mode
    _disk(db)
    event = _event(db)
    fake = FakePBS()
    fake.datastores = [{"name": "other", "path": "/mnt/datastore/other"}]
    client = make_backend(fake).client
    health = datastore_health.choose_source(db, "other", client=client)
    assert health["source"] == {"name": "other", "state": "ok"} and health["blocked_events"] == []
    db.refresh(event)
    assert event.datastore == "other"
    with pytest.raises(Exception):
        datastore_health.choose_source(db, "nope", client=client)


def test_source_candidates_exclude_rotation_disks(api_mode):
    fake = FakePBS()
    fake.datastores.append({"name": TARGET, "path": ".", "backing-device": "U", "comment": pbo_marker(SERIAL)})
    items = datastore_health.list_source_candidates(api_mode, client=make_backend(fake).client)
    assert [item["name"] for item in items] == ["main"]
    assert items[0]["total"] and items[0]["used"]


# --- b) rotation disk configuration deleted ------------------------------------------------------


def _prepared(db, session_factory):
    """A disk prepared by PBO (format path), then its PBS configuration deleted
    (`proxmox-backup-manager datastore remove`, WITHOUT --destroy-data)."""
    disk = _disk(db)
    fake = FakePBS(disks=[usb_disk(SERIAL, partitions=[])])
    backend = make_backend(fake, session_factory)
    result = backend.post("/prepare-dedicated-pbs-datastore", {"disk": SERIAL, "datastore_name": TARGET, "confirmation": True})
    assert result.ok, result.message
    db.refresh(disk)
    disk.prepared_as_pbs_datastore = True
    disk.pbs_datastore_name = TARGET
    db.commit()
    # The proof PBO keeps for itself: filesystem UUID + relative path (PBS: path = name).
    assert (disk.pbs_backing_device, disk.pbs_datastore_path, disk.pbs_marker_source) == (f"UUID-{TARGET}", TARGET, "created")
    fake.datastores = [cfg for cfg in fake.datastores if cfg["name"] != TARGET]  # deleted in PBS
    fake.mount_status.pop(TARGET, None)
    fake.calls.clear()
    return disk, fake, backend


def _prepare(backend):
    return backend.post("/prepare-dedicated-pbs-datastore", {"disk": SERIAL, "datastore_name": TARGET, "confirmation": True})


def test_config_missing_same_uuid_is_recreated_without_any_wipe(api_mode, session_factory):
    db = api_mode
    disk, fake, backend = _prepared(db, session_factory)
    fake.add_snapshot(TARGET, "vm/100", 1_700_000_000)  # the backups are still on the disk
    health = datastore_health.check(db, client=backend.client)
    assert health["disks"][str(disk.id)] == {"state": "config_missing", "datastore": TARGET, "recoverable": True, "marker_source": "created"}
    assert "pbs_config_missing" not in [r["code"] for r in backup_readiness.local_disk_reasons(db, disk)]

    from app.models import BackupRunStatus, ExternalBackupMode, ExternalBackupRun
    from app.services.run_log import run_journal

    run = ExternalBackupRun(
        disk_id=disk.id, status=BackupRunStatus.RUNNING, started_at=NOW, created_at=NOW, target_path="/x", datastore_name="main",
        mode=ExternalBackupMode.DEDICATED,
    )
    db.add(run)
    db.commit()
    result = backend.post(
        "/prepare-dedicated-pbs-datastore", {"disk": SERIAL, "datastore_name": TARGET, "confirmation": True, "callback_run_id": run.id}
    )
    assert result.ok, result.message
    assert _wiped(fake) == []  # NEVER formatted
    db.refresh(run)
    codes = [entry["code"] for entry in run_journal(run, disk)["entries"]]
    assert "datastore_recreate_start" in codes and "datastore_recreated" in codes
    assert fake.reused == [
        {"name": TARGET, "path": TARGET, "backing-device": f"UUID-{TARGET}", "reuse-datastore": True, "comment": pbo_marker(SERIAL), "tuning": "sync-level=file"}
    ]
    assert fake.mount_status[TARGET] == "mounted"
    assert result.message == "PBS configuration re-created from the existing data, no formatting."
    assert datastore_health.check(db, client=backend.client)["disks"][str(disk.id)]["state"] == "ok"


def test_marker_backfilled_from_the_run_journal(api_mode, session_factory):
    """A disk prepared before PBO kept its own proof: the UUID comes from the
    `temporarily mounting '<uuid>'` line PBS logged in an earlier run."""
    from app.models import BackupRunStatus, ExternalBackupMode, ExternalBackupRun

    db = api_mode
    disk, fake, backend = _prepared(db, session_factory)
    disk.pbs_backing_device = disk.pbs_datastore_path = disk.pbs_marker_source = None
    db.add(
        ExternalBackupRun(
            disk_id=disk.id, status=BackupRunStatus.SUCCESS, started_at=NOW, created_at=NOW, target_path="/x", datastore_name="main",
            mode=ExternalBackupMode.DEDICATED,
            stdout_log=f"[2026-11-16T01:00:00Z] mount_datastore 2026-11-16T02:00:01+01:00: temporarily mounting 'UUID-{TARGET}' to '/run/proxmox-backup/mount/1a2b'",
        )
    )
    db.commit()
    health = datastore_health.check(db, client=backend.client)
    db.refresh(disk)
    assert (disk.pbs_backing_device, disk.pbs_datastore_path, disk.pbs_marker_source) == (f"UUID-{TARGET}", TARGET, "run_log")
    assert health["disks"][str(disk.id)]["recoverable"] is True
    assert _prepare(backend).ok
    assert _wiped(fake) == [] and len(fake.reused) == 1


def test_marker_backfilled_from_the_pbs_config_while_it_still_exists(api_mode, session_factory):
    db = api_mode
    disk = _disk(db, prepared_as_pbs_datastore=True, pbs_datastore_name=TARGET)
    configs = [{"name": TARGET, "path": TARGET, "backing-device": "UUID-X", "comment": pbo_marker(SERIAL)}]
    from app.services.pbs_marker import backfill

    assert backfill(db, configs) == [disk.id]
    assert (disk.pbs_backing_device, disk.pbs_datastore_path, disk.pbs_marker_source) == ("UUID-X", TARGET, "pbs_config")


def test_config_missing_other_uuid_is_refused_never_wiped(api_mode, session_factory):
    db = api_mode
    disk, fake, backend = _prepared(db, session_factory)
    disk.pbs_backing_device = "UUID-of-a-previous-filesystem"
    db.commit()
    with pytest.raises(HostAgentError) as refused:
        _prepare(backend)
    assert refused.value.payload.get("error_code") == "pbs_config_missing_uuid_mismatch"
    assert "Nothing is formatted" in str(refused.value) and "--reuse-datastore true" in str(refused.value)
    assert _wiped(fake) == [] and fake.reused == []


def test_config_missing_unknown_uuid_is_refused_never_wiped(api_mode, session_factory):
    db = api_mode
    disk, fake, backend = _prepared(db, session_factory)
    disk.pbs_backing_device = None  # nothing recorded, nothing in the journals
    db.commit()
    health = datastore_health.check(db, client=backend.client)
    assert health["disks"][str(disk.id)]["recoverable"] is False
    assert "pbs_config_missing" in [r["code"] for r in backup_readiness.local_disk_reasons(db, disk)]
    with pytest.raises(HostAgentError) as refused:
        _prepare(backend)
    assert refused.value.payload.get("error_code") == "pbs_config_missing_unknown"
    assert _wiped(fake) == [] and fake.reused == []


def test_recreated_but_empty_datastore_stops_the_run(api_mode, session_factory):
    """Re-created at the wrong place: PBO saw backups on this disk, PBS shows none."""
    db = api_mode
    disk, fake, backend = _prepared(db, session_factory)
    disk.contents_cache = {"items": [{"group": "vm/100", "versions": 2}], "snapshots": ["vm/100/1"]}
    db.commit()
    with pytest.raises(HostAgentError) as refused:
        _prepare(backend)
    assert refused.value.payload.get("error_code") == "recreated_datastore_empty"
    assert _wiped(fake) == []


def test_datastore_problems_are_disk_alerts(api_mode, session_factory):
    from app.services.disk_guards import disk_alerts

    db = api_mode
    disk, fake, backend = _prepared(db, session_factory)
    datastore_health.check(db, client=backend.client)
    assert [(a["code"], a["level"]) for a in disk_alerts(db) if a["disk_id"] == disk.id] == [("pbs_config_recoverable", "warning")]
    disk.pbs_backing_device = None
    db.commit()
    datastore_health.check(db, client=backend.client)
    assert [a["code"] for a in disk_alerts(db) if a["disk_id"] == disk.id] == ["pbs_config_missing"]


# --- c) expected name taken by a datastore PBO did not create -----------------------------------


def test_datastore_with_expected_name_but_no_marker_is_a_conflict(api_mode):
    db = api_mode
    disk = _disk(db)
    fake = FakePBS()
    fake.datastores.append({"name": TARGET, "path": "/mnt/datastore/x", "comment": "made by hand"})
    health = datastore_health.check(db, client=make_backend(fake).client)
    assert health["disks"][str(disk.id)]["state"] == "conflict"
    assert "pbs_datastore_conflict" in [r["code"] for r in backup_readiness.local_disk_reasons(db, disk)]
    assert fake.mutating_calls() == []  # nothing automatic


def test_pbs_unreachable_keeps_the_previous_verdict(api_mode):
    db = api_mode
    _disk(db)
    fake = FakePBS()
    fake.datastores = []
    datastore_health.check(db, client=make_backend(fake).client)

    class Down:
        def list_datastores(self):
            raise RuntimeError("PBS down")

    health = datastore_health.check(db, client=Down())
    assert health["error"] == "PBS down" and health["source"]["state"] == "missing"


def test_routes(client, monkeypatch):
    assert client.get("/api/v1/datastores/health").status_code == 401
    assert client.post("/api/v1/setup/admin", json=ADMIN).status_code == 201
    assert client.get("/api/v1/datastores/health").json()["checked"] is False
    monkeypatch.setattr(datastore_health, "list_source_candidates", lambda db: [{"name": "main", "total": 1, "used": 0, "removable": False}])
    body = client.get("/api/v1/datastores").json()
    assert body["items"][0]["name"] == "main"
