"""B49: USB device present ≠ disk usable; reactivation; live check before a
run; verification only after the passthrough."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from app.models import ActivityEvent, BackupRunStatus, ExternalBackupMode, ExternalBackupRun, ExternalDisk
from app.schemas.agent import AgentDiskReportCreate
from app.services import backup_readiness, disk_presence, events, run_events
from app.services.disk_eject import mark_disk_ejected
from app.services.disks import ingest_agent_disk_report
from app.services.host_agent import HostAgentError, HostAgentResult

PORT = {"busnum": 2, "devpath": "3", "port_id": "2-3", "vendor_id": "174c", "product_id": "55aa", "serial": "MSFT30"}
USB = {"port_id": "2-3", "vendor_id": "174c", "product_id": "55aa", "serial": "MSFT30"}


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    events.bus.reset()
    run_events.reset_agent_presence()
    for name in ("handle_disk_detected",):
        monkeypatch.setattr(f"app.services.disks.{name}", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.services.notifications.unsafe_removal", lambda *a: None)
    yield
    events.bus.reset()


def _report(db, disks, usb=(USB,)):
    payload = AgentDiskReportCreate(
        hostname="pve", observed_at=datetime.now(timezone.utc), disks=disks, usb_devices=list(usb), trigger="udev"
    )
    return ingest_agent_disk_report(db, payload)


def _item():
    return {"serial_number": "ROT1", "display_name": "Rotation A", "capacity_gb": 2000, "candidate_type": "usb", "connected": True, "usb_location": PORT}


def _disk(db, ejected=True) -> ExternalDisk:
    _report(db, [_item()])
    disk = db.query(ExternalDisk).filter_by(serial_number="ROT1").one()
    disk.trusted = True
    disk.dedicated_backup_disk = True
    if ejected:
        mark_disk_ejected(disk)
    db.commit()
    return disk


# --- 1. Ejected while its USB device is there --------------------------------------------------


def test_powered_off_by_the_eject_stays_ejected_even_after_an_agent_restart(db):
    disk = _disk(db)
    _report(db, [])  # spun down / powered off: no block device, USB device still on the bus
    db.refresh(disk)
    assert (disk.status, disk.connected) == ("ejected", False)
    _report(db, [_item()])  # agent restarted, block device back: still not usable
    db.refresh(disk)
    assert disk.status == "ejected"
    codes = [reason["code"] for reason in backup_readiness.local_disk_reasons(db, disk)]
    assert codes == ["disk_ejected"]


def test_only_leaving_the_usb_bus_ends_the_eject(db):
    disk = _disk(db)
    _report(db, [], usb=())  # unplugged
    db.refresh(disk)
    assert (disk.handoff_status, disk.status) == (None, "unplugged")
    _report(db, [_item()])  # plugged back
    db.refresh(disk)
    assert (disk.status, disk.connected) == ("known", True)


def test_a_usable_disk_whose_block_device_vanishes_is_ejected_not_plugged(db):
    disk = _disk(db, ejected=False)
    _report(db, [])  # block device gone, USB device still there
    db.refresh(disk)
    assert disk.status == "ejected"


def test_a_fresh_plug_is_never_marked_ejected(db):
    disk = _disk(db, ejected=False)
    _report(db, [], usb=())  # unplugged
    _report(db, [])  # USB device enumerated, block device not there yet
    db.refresh(disk)
    assert disk.status == "unplugged" and disk.handoff_status is None


# --- 2. Reactivate -------------------------------------------------------------------------------


class FakeHost:
    def __init__(self, *, found=False, usb=True, reactivate=None, error=None):
        self.found, self.usb, self.reactivate, self.error = found, usb, reactivate, error
        self.calls = []

    def post(self, path, payload):
        self.calls.append((path, payload))
        if path == "/disk/usb-location":
            return HostAgentResult(True, "", None, None, None, None, None, {"ok": True, "found": self.found})
        if self.error:
            raise self.error
        answer = self.reactivate or {"ok": True, "method": "authorized", "device": "sdb"}
        return HostAgentResult(bool(answer.get("ok")), "", None, None, None, None, None, answer)

    def get(self, path, params=None):
        return HostAgentResult(True, "", None, None, None, None, None, {"devices": [USB] if self.usb else []})


def test_reactivate_brings_the_disk_back(db):
    disk = _disk(db)
    host = FakeHost()
    disk = disk_presence.reactivate(db, disk, client=host)
    assert (disk.status, disk.connected) == ("known", True)
    assert host.calls == [("/disk/reactivate", {"serial": "ROT1", "port_id": "2-3", "vendor_id": "174c", "product_id": "55aa", "usb_serial": "MSFT30"})]
    assert db.query(ActivityEvent).filter_by(action="reactivated").count() == 1


@pytest.mark.parametrize(
    ("host", "code"),
    [
        (FakeHost(reactivate={"ok": False, "error_code": "block_device_missing", "message": "The disk did not come back."}), "reactivate_failed"),
        (FakeHost(error=HostAgentError("404", stdout_log=None, stderr_log=None, command_summary=None, execution_cwd=None, return_code=404, status_code=404)), "reactivate_agent_outdated"),
    ],
)
def test_reactivate_failures_say_unplug_and_plug_back(db, host, code):
    disk = _disk(db)
    with pytest.raises(HTTPException) as error:
        disk_presence.reactivate(db, disk, client=host)
    assert error.value.detail["code"] == code
    db.refresh(disk)
    assert disk.status == "ejected"


def test_only_an_ejected_disk_is_reactivated(db):
    disk = _disk(db, ejected=False)
    with pytest.raises(HTTPException) as error:
        disk_presence.reactivate(db, disk, client=FakeHost())
    assert error.value.detail["code"] == "reactivate_not_ejected"


# --- 3. Live check right before creating a run ---------------------------------------------------


def test_a_disk_absent_now_creates_no_run_and_offers_reactivate(db):
    disk = _disk(db, ejected=False)
    with pytest.raises(HTTPException) as error:
        disk_presence.require_present(db, disk, client=FakeHost(found=False, usb=True))
    assert error.value.status_code == 409 and error.value.detail["code"] == "disk_not_present_reactivable"
    db.refresh(disk)
    assert disk.status == "ejected"  # the Reactivate button shows up
    with pytest.raises(HTTPException) as gone:
        disk_presence.require_present(db, _disk(db, ejected=False), client=FakeHost(found=False, usb=False))
    assert gone.value.detail["code"] == "disk_not_present"
    assert db.query(ExternalBackupRun).count() == 0


def test_present_or_unknown_lets_the_run_start(db):
    disk = _disk(db, ejected=False)
    disk_presence.require_present(db, disk, client=FakeHost(found=True))
    disk_presence.require_present(db, disk)  # no host agent configured: the hand-off decides


# --- 4. Flags left by a failure before the passthrough -------------------------------------------


def _flagged(db, log):
    disk = _disk(db, ejected=False)
    start = datetime.utcnow() - timedelta(minutes=5)
    run = ExternalBackupRun(
        disk_id=disk.id, status=BackupRunStatus.FAILED, started_at=start, created_at=start, finished_at=start, target_path="/x",
        datastore_name="main", mode=ExternalBackupMode.DEDICATED, current_step="handoff_disk_not_plugged", stdout_log=log,
    )
    db.add(run)
    disk.verify_recommended = True
    db.add(ActivityEvent(created_at=start + timedelta(seconds=30), category="disk", action="unsafe_removal", status="warning",
                         summary="x", details={"disk_id": disk.id, "reason": "during_run"}))
    db.commit()
    return disk


def test_existing_flag_from_a_failure_before_the_passthrough_is_cleared(db):
    disk = _flagged(db, "[2026-11-18T01:00:00Z] handoff_disk Handing off USB disk to PBS VM.")
    assert disk_presence.clear_mistaken_verify_flags(db) == 1
    db.refresh(disk)
    assert not disk.verify_recommended
    assert disk_presence.clear_mistaken_verify_flags(db) == 0  # idempotent


def test_a_flag_after_the_passthrough_is_kept(db):
    disk = _flagged(db, "[2026-11-18T01:00:00Z] run_export Starting PBS datastore export to dedicated disk.")
    assert disk_presence.clear_mistaken_verify_flags(db) == 0
    db.refresh(disk)
    assert disk.verify_recommended is True
