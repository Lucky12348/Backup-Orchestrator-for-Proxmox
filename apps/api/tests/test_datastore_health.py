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
    """A disk prepared by PBO (format path), then its PBS configuration deleted."""
    disk = _disk(db)
    fake = FakePBS(disks=[usb_disk(SERIAL, partitions=[])])
    backend = make_backend(fake, session_factory)
    result = backend.post("/prepare-dedicated-pbs-datastore", {"disk": SERIAL, "datastore_name": TARGET, "confirmation": True})
    assert result.ok, result.message
    db.refresh(disk)
    disk.prepared_as_pbs_datastore = True
    disk.pbs_datastore_name = TARGET
    db.commit()
    assert disk.pbs_backing_device == f"UUID-{TARGET}" and disk.pbs_datastore_path == "."  # the PBO marker
    fake.datastores = [cfg for cfg in fake.datastores if cfg["name"] != TARGET]  # deleted in PBS
    fake.calls.clear()
    return disk, fake, backend


def test_config_missing_is_reported_then_recreated_with_reuse_never_wiped(api_mode, session_factory):
    db = api_mode
    disk, fake, backend = _prepared(db, session_factory)
    health = datastore_health.check(db, client=backend.client)
    assert health["disks"][str(disk.id)] == {"state": "config_missing", "datastore": TARGET, "recoverable": True}
    assert "pbs_config_missing" not in [r["code"] for r in backup_readiness.local_disk_reasons(db, disk)]

    result = backend.post("/prepare-dedicated-pbs-datastore", {"disk": SERIAL, "datastore_name": TARGET, "confirmation": True})
    assert result.ok, result.message
    assert _wiped(fake) == []  # NEVER formatted
    assert fake.reused == [
        {"name": TARGET, "path": ".", "backing-device": f"UUID-{TARGET}", "reuse-datastore": True, "comment": pbo_marker(SERIAL)}
    ]
    assert datastore_health.check(db, client=backend.client)["disks"][str(disk.id)]["state"] == "ok"


def test_config_missing_without_marker_is_refused_and_never_wiped(api_mode, session_factory):
    db = api_mode
    disk, fake, backend = _prepared(db, session_factory)
    disk.pbs_backing_device = None  # prepared before Phase 6c: no PBO marker recorded
    db.commit()
    health = datastore_health.check(db, client=backend.client)
    assert health["disks"][str(disk.id)]["recoverable"] is False
    assert "pbs_config_missing" in [r["code"] for r in backup_readiness.local_disk_reasons(db, disk)]
    with pytest.raises(HostAgentError) as refused:
        backend.post("/prepare-dedicated-pbs-datastore", {"disk": SERIAL, "datastore_name": TARGET, "confirmation": True})
    assert refused.value.payload.get("error_code") == "pbs_config_missing_unrecoverable"
    assert "nothing is formatted" in str(refused.value)
    assert _wiped(fake) == [] and fake.reused == []


def test_marker_of_another_filesystem_is_not_trusted(api_mode, session_factory):
    db = api_mode
    disk, fake, backend = _prepared(db, session_factory)
    disk.pbs_backing_device = "UUID-of-a-previous-filesystem"
    db.commit()
    with pytest.raises(HostAgentError):
        backend.post("/prepare-dedicated-pbs-datastore", {"disk": SERIAL, "datastore_name": TARGET, "confirmation": True})
    assert _wiped(fake) == [] and fake.reused == []


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
