"""Deterministic disk ↔ USB port matching (agent-reported sysfs location)."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, inspect, text

from app.models.external_disk import ExternalDisk
from app.schemas.agent import AgentDiskReportCreate
from app.services.disk_handoff import (
    DISK_NOT_PLUGGED,
    USB_DEVICE_AMBIGUOUS,
    USB_LOCATION_NOT_IN_PROXMOX,
    DiskHandoffError,
    current_usb_location,
    resolve_usb_device,
)
from app.services.disks import ingest_agent_disk_report
from app.services.host_agent import HostAgentError, HostAgentResult

# What GET /nodes/{node}/hardware/usb returned on pve-test: no USB serials.
HARDDRIVE_2_3 = {"product": "QEMU USB HARDDRIVE", "manufacturer": "QEMU", "busnum": 2, "devnum": 2, "usbpath": "3", "class": 0, "vendid": "46f4", "prodid": "0001"}
TABLET_3_1 = {"product": "QEMU USB Tablet", "manufacturer": "QEMU", "busnum": 3, "devnum": 2, "usbpath": "1", "class": 0, "vendid": "0627", "prodid": "0001"}
HARDDRIVE_2_4 = {**HARDDRIVE_2_3, "devnum": 3, "usbpath": "4"}


def _disk(serial="PBOTESTA", location=None, seen_minutes_ago=1, **extra):
    return SimpleNamespace(
        serial_number=serial,
        canonical_serial_number=serial,
        reported_serial_number=None,
        serial_aliases=[],
        model_name="QEMU HARDDISK",
        display_name="QEMU HARDDISK",
        candidate_type="usb",
        usb_location=location,
        usb_location_seen_at=datetime.utcnow() - timedelta(minutes=seen_minutes_ago) if location else None,
        **extra,
    )


def _loc(port_id, device="sdb"):
    busnum, devpath = port_id.split("-", 1)
    return {"port_id": port_id, "busnum": int(busnum), "devpath": devpath, "device": device}


class FakeAgent:
    def __init__(self, payload=None, error=None):
        self.payload = payload
        self.error = error
        self.calls = []

    def post(self, path, payload):
        self.calls.append((path, payload))
        if self.error:
            raise self.error
        return HostAgentResult(ok=True, message="", stdout_log=None, stderr_log=None, command_summary=None,
                               execution_cwd=None, return_code=0, payload=self.payload)


# --- Disk without USB serial + QEMU tablet (the pve-test case) ---------------------------


def test_location_picks_the_disk_not_the_tablet():
    match = resolve_usb_device([HARDDRIVE_2_3, TABLET_3_1], _disk(), _loc("2-3"))
    assert match["mapping"] == "2-3" and match["source"] == "usb_location"


def test_fallback_without_location_excludes_the_tablet():
    match = resolve_usb_device([TABLET_3_1, HARDDRIVE_2_3], _disk(), None)
    assert match["mapping"] == "2-3"


@pytest.mark.parametrize(
    "device",
    [
        {**TABLET_3_1, "product": "Optical Mouse"},
        {**TABLET_3_1, "product": "USB Keyboard"},
        {**TABLET_3_1, "product": "Generic Device", "class": 3},  # HID
        {**TABLET_3_1, "product": "Generic Device", "class": "09"},  # hub
        {**TABLET_3_1, "product": "Generic Device", "class": "0x0e"},  # video
    ],
)
def test_fallback_never_offers_non_storage_devices(device):
    assert resolve_usb_device([device, HARDDRIVE_2_3], _disk(), None)["mapping"] == "2-3"


# --- Two identical disks without serial on two ports -------------------------------------------


def test_two_identical_disks_each_get_their_own_port():
    devices = [HARDDRIVE_2_3, HARDDRIVE_2_4]
    assert resolve_usb_device(devices, _disk("DISKA"), _loc("2-3"))["mapping"] == "2-3"
    assert resolve_usb_device(devices, _disk("DISKB"), _loc("2-4", "sdc"))["mapping"] == "2-4"


def test_two_identical_disks_without_location_are_refused_not_guessed():
    with pytest.raises(DiskHandoffError) as info:
        resolve_usb_device([HARDDRIVE_2_3, HARDDRIVE_2_4], _disk(), None)
    assert info.value.code == USB_DEVICE_AMBIGUOUS
    assert "2-3" in info.value.technical and "2-4" in info.value.technical


def test_known_port_missing_from_proxmox_is_an_error_not_a_guess():
    with pytest.raises(DiskHandoffError) as info:
        resolve_usb_device([HARDDRIVE_2_4, TABLET_3_1], _disk(), _loc("2-3"))
    assert info.value.code == USB_LOCATION_NOT_IN_PROXMOX
    assert "2-3" in info.value.technical


# --- Disk plugged back into another port -----------------------------------------------------------


def test_replugged_disk_uses_the_new_port_and_logs_it():
    disk = _disk(location=_loc("2-3"))
    agent = FakeAgent({"ok": True, "found": True, "device": "sdc", "usb_location": _loc("2-4", "sdc")})
    logged = []
    location = current_usb_location(None, disk, lambda step, message, line: logged.append(message), agent_client=agent)
    assert location["port_id"] == "2-4"
    assert disk.usb_location["port_id"] == "2-4" and disk.usb_location_seen_at is not None
    assert any("moved from USB port 2-3 to 2-4" in message for message in logged)
    assert agent.calls == [("/disk/usb-location", {"serial": "PBOTESTA", "device": "sdb"})]
    assert resolve_usb_device([HARDDRIVE_2_3, HARDDRIVE_2_4], disk, location)["mapping"] == "2-4"


def test_unplugged_disk_stops_the_handoff():
    with pytest.raises(DiskHandoffError) as info:
        current_usb_location(None, _disk(location=_loc("2-3")), agent_client=FakeAgent({"ok": True, "found": False}))
    assert info.value.code == DISK_NOT_PLUGGED and info.value.status_code == 409


def test_old_agent_falls_back_to_a_recent_location_only():
    error = HostAgentError("not served", stdout_log=None, stderr_log=None, command_summary=None,
                           execution_cwd=None, return_code=404, status_code=404)
    recent = _disk(location=_loc("2-3"), seen_minutes_ago=2)
    assert current_usb_location(None, recent, agent_client=FakeAgent(error=error))["port_id"] == "2-3"
    stale = _disk(location=_loc("2-3"), seen_minutes_ago=60)
    assert current_usb_location(None, stale, agent_client=FakeAgent(error=error)) is None
    assert current_usb_location(None, _disk(), agent_client=FakeAgent(error=error)) is None


def test_non_usb_disk_falls_back_to_serial_matching():
    agent = FakeAgent({"ok": True, "found": True, "device": "sdb", "usb_location": None})
    assert current_usb_location(None, _disk(), agent_client=agent) is None


# --- Report ingestion + migration --------------------------------------------------------------------


def test_report_stores_the_usb_location(db, monkeypatch):
    for name in ("notify_new_disk_detected", "notify_known_disk_detected", "handle_disk_detected"):
        monkeypatch.setattr(f"app.services.disks.{name}", lambda *a, **k: None)
    payload = AgentDiskReportCreate(
        hostname="pve",
        observed_at=datetime.now(timezone.utc),
        disks=[{
            "serial_number": "PBOTEST-a", "display_name": "QEMU", "capacity_gb": 100, "candidate_type": "usb", "connected": True,
            "usb_location": {"busnum": 2, "devpath": "3", "port_id": "2-3", "device": "sdb", "vendor_id": "46f4", "serial": None},
        }],
    )
    ingest_agent_disk_report(db, payload)
    disk = db.query(ExternalDisk).filter(ExternalDisk.serial_number != "agent-report::pve").one()
    assert disk.usb_location["port_id"] == "2-3" and disk.usb_location["device"] == "sdb"
    assert disk.usb_location_seen_at is not None


def test_report_rejects_a_malformed_port():
    with pytest.raises(ValidationError):
        AgentDiskReportCreate(
            hostname="pve",
            observed_at=datetime.now(timezone.utc),
            disks=[{"serial_number": "S", "display_name": "d", "capacity_gb": 1, "connected": True,
                    "usb_location": {"busnum": 2, "devpath": "3;reboot", "port_id": "2-3"}}],
        )


def test_migration_adds_columns_on_an_upgraded_database(tmp_path):
    from app.db.migrate import run_migrations

    engine = create_engine(f"sqlite:///{tmp_path / 'u.sqlite'}")
    with engine.begin() as connection:  # legacy table without the new columns
        connection.execute(text("CREATE TABLE external_disks (id INTEGER PRIMARY KEY, serial_number VARCHAR(255))"))
    run_migrations(engine)
    columns = {column["name"] for column in inspect(engine).get_columns("external_disks")}
    assert {"usb_location", "usb_location_seen_at"} <= columns
    run_migrations(engine)  # idempotent
    engine.dispose()


def test_migration_on_a_fresh_database(tmp_path):
    from app.db.base import Base
    from app.db.init import legacy_tables
    from app.db.migrate import run_migrations

    engine = create_engine(f"sqlite:///{tmp_path / 'f.sqlite'}")
    Base.metadata.create_all(engine, tables=legacy_tables())  # fresh install: columns already there
    run_migrations(engine)
    assert "usb_location" in {column["name"] for column in inspect(engine).get_columns("external_disks")}
    engine.dispose()


def test_ui_knows_every_handoff_message():
    """apps/web/src/handoff.ts translates the run messages: same strings as the API."""
    import re
    from pathlib import Path

    from app.services import disk_handoff

    source = (Path(__file__).resolve().parents[2] / "web" / "src" / "handoff.ts").read_text(encoding="utf-8")
    block = source.split("HANDOFF_MESSAGES", 1)[1]
    ui_messages = dict(re.findall(r'"([^"]+)":\s*\n?\s*"([a-z_]+)"', block))
    api_messages = {
        disk_handoff.USB_DEVICE_NOT_FOUND_MESSAGE: disk_handoff.USB_DEVICE_NOT_FOUND,
        "The disk's USB port is not in the Proxmox USB device list.": disk_handoff.USB_LOCATION_NOT_IN_PROXMOX,
        "The disk is no longer plugged into the Proxmox host.": disk_handoff.DISK_NOT_PLUGGED,
        disk_handoff.USB_DEVICE_AMBIGUOUS_MESSAGE: disk_handoff.USB_DEVICE_AMBIGUOUS,
    }
    assert ui_messages == api_messages
    handoff_source = Path(disk_handoff.__file__).read_text(encoding="utf-8")
    for message in api_messages:
        if message not in (disk_handoff.USB_DEVICE_NOT_FOUND_MESSAGE, disk_handoff.USB_DEVICE_AMBIGUOUS_MESSAGE):
            assert f'"{message}"' in handoff_source
