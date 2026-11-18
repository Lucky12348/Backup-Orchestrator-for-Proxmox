"""Phase 5b: disk states (B25 "ejected" until unplugged), anti double click
(B25), approval flow (decision) and "Run detection again" (rescan)."""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.models.external_backup_run import ExternalBackupRun
from app.models.external_disk import ExternalDisk
from app.schemas.agent import AgentDiskReportCreate
from app.services import backup_readiness, events, run_events
from app.services.disk_eject import mark_disk_ejected
from app.services.disks import ingest_agent_disk_report
from app.services.host_agent import HostAgentError

ADMIN = {"username": "admin", "password": "correct horse battery"}


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    events.bus.reset()
    run_events.reset_agent_presence()
    for name in ("handle_disk_detected",):
        monkeypatch.setattr(f"app.services.disks.{name}", lambda *args, **kwargs: None)
    yield
    events.bus.reset()


def _types(since: int = 0):
    return [(event.type, event.data) for event in events.bus.since(since)]


def _last_id() -> int:
    published = events.bus.since(0)
    return published[-1].id if published else 0


def _report(db, disks):
    payload = AgentDiskReportCreate(hostname="pve", observed_at=datetime.now(timezone.utc), disks=disks, trigger="udev")
    return ingest_agent_disk_report(db, payload)


def _item(serial="ROT1", **extra):
    return {
        "serial_number": serial,
        "display_name": "Rotation A",
        "model_name": "WD Elements",
        "capacity_gb": 2000,
        "candidate_type": "usb",
        "connected": True,
        **extra,
    }


def _ejected_disk(db) -> ExternalDisk:
    _report(db, [_item()])
    disk = db.query(ExternalDisk).filter_by(serial_number="ROT1").one()
    disk.trusted = True
    disk.dedicated_backup_disk = True
    mark_disk_ejected(disk)
    db.commit()
    return disk


# --- B25: "Ejected" until unplugged, then plugged back -------------------------------------


def test_ejected_disk_stays_ejected_while_plugged(db):
    disk = _ejected_disk(db)
    since = _last_id()
    _report(db, [_item()])  # timer report: the disk is still physically there
    db.refresh(disk)
    assert disk.status == "ejected" and disk.connected is False and disk.handoff_status == "ejected"
    assert [kind for kind, _ in _types(since)] == []  # no disk.added: it is not "connected"
    reasons = [reason["code"] for reason in backup_readiness.local_disk_reasons(db, disk)]
    assert reasons == ["disk_ejected"]


def test_unplug_publishes_removed_then_replug_makes_it_usable_again(db):
    disk = _ejected_disk(db)
    since = _last_id()
    _report(db, [])  # udev remove (unplugged, or powered off by the eject)
    removed = _types(since)
    assert [(kind, data["serial_number"]) for kind, data in removed] == [("disk.removed", "ROT1")]
    assert removed[0][1]["status"] == "unplugged"
    db.refresh(disk)
    assert disk.handoff_status is None and disk.status == "unplugged"

    since = _last_id()
    _report(db, [_item()])  # udev add
    db.refresh(disk)
    assert disk.connected is True and disk.status == "known"
    assert [(kind, data["status"]) for kind, data in _types(since)] == [("disk.added", "known")]
    assert backup_readiness.local_disk_reasons(db, disk) == []


def test_partitions_reported_by_the_agent_are_stored(db):
    _report(db, [_item(partitions=[{"name": "sdb1", "filesystem_type": "ntfs", "size_gb": 1863, "label": "Photos"}])])
    disk = db.query(ExternalDisk).filter_by(serial_number="ROT1").one()
    assert disk.partitions[0]["filesystem_type"] == "ntfs" and disk.partitions[0]["label"] == "Photos"
    _report(db, [_item()])  # an older agent sends nothing: keep what we knew
    db.refresh(disk)
    assert disk.partitions and disk.partitions[0]["name"] == "sdb1"


# --- B25: anti double click / readiness refusal creates no run ------------------------------


class _NoActiveExportBridge:
    def inspect_external_export_objects(self):
        return SimpleNamespace(payload={"active": False})


def _login(client):
    assert client.post("/api/v1/setup/admin", json=ADMIN).status_code == 201


def _approved_disk(session_factory, **extra) -> int:
    with session_factory() as db:
        disk = ExternalDisk(
            serial_number="ROT1", display_name="Rotation A", capacity_gb=2000, connected=True, trusted=True,
            dedicated_backup_disk=True, source="agent", active=True, presence_state="present", **extra,
        )
        db.add(disk)
        db.commit()
        return disk.id


@pytest.fixture
def backup_client(client, monkeypatch):
    _login(client)
    monkeypatch.setattr("app.services.external_backups.get_external_backup_agent_bridge", lambda: _NoActiveExportBridge())
    # The run stays PENDING: the background execution is not under test here.
    monkeypatch.setattr("app.api.routes.external_backups.execute_external_backup_run", lambda run_id: None)
    return client


def test_double_click_creates_a_single_run(backup_client, session_factory):
    disk_id = _approved_disk(session_factory)
    first = backup_client.post("/api/v1/external-backups/run", json={"disk_id": disk_id, "confirmation": True})
    second = backup_client.post("/api/v1/external-backups/run", json={"disk_id": disk_id, "confirmation": True})
    assert first.status_code == 200
    assert second.status_code == 409
    with session_factory() as db:
        assert db.query(ExternalBackupRun).count() == 1


def test_ejected_disk_is_refused_with_a_code_and_no_run(backup_client, session_factory):
    disk_id = _approved_disk(session_factory, handoff_status="ejected")
    with session_factory() as db:
        disk = db.get(ExternalDisk, disk_id)
        disk.connected = False
        db.commit()
    response = backup_client.post("/api/v1/external-backups/run", json={"disk_id": disk_id, "confirmation": True})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "disk_ejected"
    with session_factory() as db:
        assert db.query(ExternalBackupRun).count() == 0


def test_unapproved_disk_is_refused_with_a_code_and_no_run(backup_client, session_factory):
    disk_id = _approved_disk(session_factory)
    with session_factory() as db:
        db.get(ExternalDisk, disk_id).trusted = False
        db.commit()
    response = backup_client.post("/api/v1/external-backups/run", json={"disk_id": disk_id, "confirmation": True})
    assert response.status_code == 409 and response.json()["detail"]["code"] == "disk_not_approved"
    with session_factory() as db:
        assert db.query(ExternalBackupRun).count() == 0


# --- Approval flow ---------------------------------------------------------------------------


def _create(session_factory, **extra) -> int:
    with session_factory() as db:
        values = {
            "serial_number": "NEW1", "display_name": "Samsung T7", "capacity_gb": 1000, "connected": True,
            "trusted": False, "dedicated_backup_disk": True, "source": "agent", "active": True, "presence_state": "present",
        }
        values.update(extra)
        disk = ExternalDisk(**values)
        db.add(disk)
        db.commit()
        return disk.id


def test_approve_needs_the_disk_name_typed_back(client, session_factory):
    _login(client)
    disk_id = _create(session_factory)
    wrong = client.post(f"/api/v1/disks/{disk_id}/decision", json={"decision": "approve", "confirm_name": "samsung"})
    assert wrong.status_code == 400 and wrong.json()["detail"]["code"] == "decision_name_mismatch"
    ok = client.post(f"/api/v1/disks/{disk_id}/decision", json={"decision": "approve", "confirm_name": "Samsung T7"})
    assert ok.status_code == 200
    body = ok.json()
    assert body["trusted"] is True and body["status"] == "known" and body["operator_decision"] is None


def test_unusable_disk_cannot_be_approved(client, session_factory):
    _login(client)
    disk_id = _create(session_factory, capacity_gb=0)
    response = client.post(f"/api/v1/disks/{disk_id}/decision", json={"decision": "approve", "confirm_name": "Samsung T7"})
    assert response.status_code == 400 and response.json()["detail"]["code"] == "decision_disk_unusable"


def test_always_reject_and_ignore(client, session_factory, db):
    _login(client)
    disk_id = _create(session_factory)
    rejected = client.post(f"/api/v1/disks/{disk_id}/decision", json={"decision": "reject"}).json()
    assert rejected["status"] == "rejected" and rejected["reject_reason"] == "operator"
    assert ("disk.updated", "rejected") in [(kind, data["status"]) for kind, data in _types()]
    cleared = client.post(f"/api/v1/disks/{disk_id}/decision", json={"decision": "clear"}).json()
    assert cleared["status"] == "new"
    ignored = client.post(f"/api/v1/disks/{disk_id}/decision", json={"decision": "ignore"}).json()
    assert ignored["status"] == "ignored"


def test_ignore_lasts_until_the_disk_is_plugged_again(db):
    _report(db, [_item("IGN1")])
    disk = db.query(ExternalDisk).filter_by(serial_number="IGN1").one()
    disk.operator_decision = "ignored"
    db.commit()
    _report(db, [_item("IGN1")])  # still plugged: still ignored
    db.refresh(disk)
    assert disk.status == "ignored"
    _report(db, [])
    _report(db, [_item("IGN1")])  # plugged again: to review again
    db.refresh(disk)
    assert disk.status == "new" and disk.operator_decision is None


def test_rejected_disk_stays_rejected_after_a_replug(db):
    _report(db, [_item("REJ1")])
    disk = db.query(ExternalDisk).filter_by(serial_number="REJ1").one()
    disk.operator_decision = "rejected"
    db.commit()
    _report(db, [])
    _report(db, [_item("REJ1")])
    db.refresh(disk)
    assert disk.status == "rejected" and disk.reject_reason == "operator"


# --- "Run detection again" ---------------------------------------------------------------------


def _settings(url):
    return SimpleNamespace(host_agent_base_url=url)


def test_rescan_asks_the_host_agent_for_a_report(client, monkeypatch):
    _login(client)
    calls = []

    class _Agent:
        def post(self, path, payload):
            calls.append(path)
            return SimpleNamespace(payload={"ok": True})

    monkeypatch.setattr("app.services.disks.get_settings", lambda: _settings("https://pve:8090"))
    monkeypatch.setattr("app.services.disks.get_host_agent_client", lambda: _Agent())
    assert client.post("/api/v1/disks/rescan").status_code == 200
    assert calls == ["/disk/rescan"]


def test_rescan_errors_are_coded(client, monkeypatch):
    _login(client)
    monkeypatch.setattr("app.services.disks.get_settings", lambda: _settings(""))
    response = client.post("/api/v1/disks/rescan")
    assert response.status_code == 409 and response.json()["detail"]["code"] == "rescan_agent_missing"

    class _OldAgent:
        def post(self, path, payload):
            raise HostAgentError("Not found", stdout_log=None, stderr_log=None, command_summary=None, execution_cwd=None, return_code=None, status_code=404)

    monkeypatch.setattr("app.services.disks.get_settings", lambda: _settings("https://pve:8090"))
    monkeypatch.setattr("app.services.disks.get_host_agent_client", lambda: _OldAgent())
    response = client.post("/api/v1/disks/rescan")
    assert response.status_code == 409 and response.json()["detail"]["code"] == "rescan_agent_outdated"


def test_rescan_requires_a_session(client):
    assert client.post("/api/v1/disks/rescan").status_code == 401
