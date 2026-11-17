"""SMART health (Phase 5f): smartctl simulated, USB bridges, cache, link speed."""

import json
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent import smart
from agent.main import parse_rotational
from agent.usb_location import usb_location_from_device_path


def ata(passed=True, reallocated=0, pending=0, uncorrectable=0, exit_status=0):
    return {
        "smartctl": {"exit_status": exit_status, "messages": []},
        "smart_status": {"passed": passed},
        "ata_smart_attributes": {
            "table": [
                {"id": 5, "name": "Reallocated_Sector_Ct", "raw": {"value": reallocated}},
                {"id": 197, "name": "Current_Pending_Sector", "raw": {"value": pending}},
                {"id": 198, "name": "Offline_Uncorrectable", "raw": {"value": uncorrectable}},
            ]
        },
        "temperature": {"current": 34},
        "power_on_time": {"hours": 12000},
    }


class FakeSmartctl:
    """Answers smartctl calls: `plain` for the first call, `sat` with -d sat."""

    def __init__(self, plain, sat=None, plain_code=0, sat_code=0):
        self.plain, self.sat, self.plain_code, self.sat_code = plain, sat, plain_code, sat_code
        self.calls = []

    def __call__(self, args, **kwargs):
        self.calls.append(args)
        data, code = (self.sat, self.sat_code) if "sat" in args else (self.plain, self.plain_code)
        return subprocess.CompletedProcess(args, code, stdout=json.dumps(data) if data is not None else "", stderr="")


def which(_name):
    return "/usr/sbin/smartctl"


class ParseTests(unittest.TestCase):
    def test_healthy_disk_is_ok(self):
        result = smart.parse(ata())
        self.assertEqual(result["status"], "ok")
        self.assertEqual((result["reallocated"], result["temperature_c"], result["power_on_hours"]), (0, 34, 12000))

    def test_reallocated_sectors_are_to_watch(self):
        result = smart.parse(ata(reallocated=8, pending=1))
        self.assertEqual(result["status"], "warning")
        self.assertEqual(result["reasons"], ["reallocated_sectors", "pending_sectors"])

    def test_failed_health_is_danger(self):
        result = smart.parse(ata(passed=False, exit_status=8))
        self.assertEqual(result["status"], "danger")
        self.assertIn("smart_failed", result["reasons"])

    def test_error_log_bit_is_to_watch(self):
        self.assertEqual(smart.parse(ata(), exit_status=64)["reasons"], ["error_log"])

    def test_nvme_critical_warning_is_danger(self):
        data = {"smartctl": {"exit_status": 0}, "smart_status": {"passed": True}, "nvme_smart_health_information_log": {"critical_warning": 1, "media_errors": 0}}
        self.assertEqual(smart.parse(data)["status"], "danger")

    def test_no_smart_data_is_unknown(self):
        self.assertEqual(smart.parse({"smartctl": {"exit_status": 4}})["status"], "unknown")


class ReadTests(unittest.TestCase):
    def test_usb_bridge_retried_with_sat(self):
        run = FakeSmartctl(plain={"smartctl": {"exit_status": 1, "messages": [{"string": "Unknown USB bridge [0x152d:0x0578]"}]}}, plain_code=1, sat=ata())
        result = smart.read_smart("sdb", run=run, which=which)
        self.assertEqual(result["status"], "ok")
        self.assertIn("-d", run.calls[1])
        self.assertIn("-n", run.calls[0])  # never wakes a sleeping disk up

    def test_bridge_without_smart_is_unknown(self):
        bridge = {"smartctl": {"exit_status": 1, "messages": [{"string": "Unknown USB bridge"}]}}
        result = smart.read_smart("sdb", run=FakeSmartctl(plain=bridge, plain_code=1, sat=bridge, sat_code=1), which=which)
        self.assertEqual((result["status"], result["reasons"]), ("unknown", ["usb_bridge_no_smart"]))

    def test_disk_in_standby_keeps_the_cache(self):
        standby = {"smartctl": {"exit_status": 2, "messages": [{"string": "Device is in STANDBY mode, exit(2)"}]}}
        self.assertIsNone(smart.read_smart("sdb", run=FakeSmartctl(plain=standby, plain_code=2), which=which))

    def test_smartctl_missing(self):
        result = smart.read_smart("sdb", run=FakeSmartctl(plain=None), which=lambda _name: None)
        self.assertEqual(result["reasons"], ["smartctl_missing"])


class AnnotateTests(unittest.TestCase):
    def test_read_on_plug_then_cached(self):
        reads = []

        def reader(device):
            reads.append(device)
            return {"status": "warning", "reasons": ["reallocated_sectors"], "reallocated": 3}

        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "smart.json"
            now = datetime(2026, 11, 16, 10, tzinfo=timezone.utc)
            disks = [{"serial_number": "A", "_device": "sdb"}]
            smart.annotate(disks, added={"A"}, path=cache, reader=reader, now=now)
            self.assertEqual(disks[0]["smart"]["status"], "warning")
            # Timer report an hour later: cached, smartctl not run again.
            later = [{"serial_number": "A", "_device": "sdb"}]
            smart.annotate(later, added=set(), path=cache, reader=reader, now=now + timedelta(hours=1))
            self.assertEqual((reads, later[0]["smart"]["reasons"]), (["sdb"], ["reallocated_sectors"]))
            # Plugged in again: read again.
            smart.annotate([{"serial_number": "A", "_device": "sdc"}], added={"A"}, path=cache, reader=reader, now=now + timedelta(hours=2))
            self.assertEqual(reads, ["sdb", "sdc"])
            # A day later: refreshed.
            smart.annotate([{"serial_number": "A", "_device": "sdc"}], added=set(), path=cache, reader=reader, now=now + timedelta(hours=27))
            self.assertEqual(len(reads), 3)


class HardwareTests(unittest.TestCase):
    def test_usb_link_speed_is_read_in_sysfs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            usb = root / "devices" / "usb1" / "1-2"
            block = usb / "1-2_1.0" / "host6" / "target6_0_0" / "6_0_0_0"
            block.mkdir(parents=True)
            for name, value in {"busnum": "1", "devpath": "2", "speed": "480"}.items():
                (usb / name).write_text(value + "\n")
            location = usb_location_from_device_path(block, stop_at=root)
            self.assertEqual((location["port_id"], location["speed_mbps"]), ("1-2", 480.0))

    def test_rotational_flag(self):
        self.assertEqual([parse_rotational(v) for v in (True, False, "1", "0", None)], [True, False, True, False, None])


if __name__ == "__main__":
    unittest.main()


class UsbDevicesTests(unittest.TestCase):
    """Phase 5f.2: a disk passed through to the PBS VM keeps its USB entry in
    sysfs; only unplugging removes it (no block device to watch then)."""

    def test_present_usb_devices_without_hubs(self):
        from agent.usb_location import list_usb_devices

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            devices = root / "bus" / "usb" / "devices"
            for name, values in {
                "usb3": {"busnum": "3", "devpath": "0", "bDeviceClass": "09"},  # root hub
                "3-1": {"busnum": "3", "devpath": "1", "bDeviceClass": "09"},  # hub
                "3-4": {"busnum": "3", "devpath": "4", "bDeviceClass": "00", "idVendor": "152d", "serial": "ABC"},
                "3-4_1.0": {},  # interface, no busnum
            }.items():
                (devices / name).mkdir(parents=True)
                for key, value in values.items():
                    (devices / name / key).write_text(value + "\n")
            found = list_usb_devices(sys_root=root)
            self.assertEqual([(d["port_id"], d["vendor_id"], d["serial"]) for d in found], [("3-4", "152d", "ABC")])
            self.assertEqual(list_usb_devices(sys_root=root / "missing"), [])
