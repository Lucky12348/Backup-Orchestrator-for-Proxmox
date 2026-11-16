import os
import tempfile
from pathlib import Path
from unittest import TestCase, skipUnless
from unittest.mock import patch

from fastapi.testclient import TestClient

from agent import server
from agent.main import AgentSettings, build_disk_report, disk_usb_location_result
from agent.usb_location import usb_location, usb_location_from_device_path


def _usb_device(directory: Path, busnum: str, devpath: str, **attrs: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "busnum").write_text(f"{busnum}\n")
    (directory / "devpath").write_text(f"{devpath}\n")
    for name, value in attrs.items():
        (directory / name).write_text(f"{value}\n")


def _tree(root: Path, port: str, busnum: str = "2", **attrs: str) -> Path:
    """sysfs-like tree: root hub usbN > port device > interface > scsi host > disk
    (":" replaced by "_" in names so the test also runs on Windows)."""
    controller = root / "devices" / "pci0000_00" / "0000_00_14.0"
    _usb_device(controller / f"usb{busnum}", busnum, "0")  # root hub: skipped
    device = controller / f"usb{busnum}" / f"{busnum}-{port}"
    _usb_device(device, busnum, port, **attrs)
    scsi = device / f"{busnum}-{port}_1.0" / "host6" / "target6_0_0" / "6_0_0_0"
    scsi.mkdir(parents=True)
    return scsi


class UsbLocationTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_disk_without_usb_serial(self):
        scsi = _tree(self.root, "3", idVendor="46f4", idProduct="0001", product="QEMU USB HARDDRIVE")
        location = usb_location_from_device_path(scsi, stop_at=self.root)
        self.assertEqual(location["port_id"], "2-3")
        self.assertEqual((location["busnum"], location["devpath"]), (2, "3"))
        self.assertIsNone(location["serial"])
        self.assertEqual(location["product"], "QEMU USB HARDDRIVE")

    def test_disk_behind_a_hub(self):
        scsi = _tree(self.root, "1.4", busnum="3", serial="ABC123")
        location = usb_location_from_device_path(scsi, stop_at=self.root)
        self.assertEqual(location["port_id"], "3-1.4")
        self.assertEqual(location["serial"], "ABC123")

    def test_not_a_usb_disk(self):
        sata = self.root / "devices" / "pci0000_00" / "ata1" / "host0" / "target0_0_0" / "0_0_0_0"
        sata.mkdir(parents=True)
        self.assertIsNone(usb_location_from_device_path(sata, stop_at=self.root))

    @skipUnless(hasattr(os, "symlink"), "symlinks unavailable")
    def test_resolves_the_block_device_link(self):
        scsi = _tree(self.root, "3")
        (self.root / "block" / "sdb").mkdir(parents=True)
        try:
            os.symlink(scsi, self.root / "block" / "sdb" / "device")
        except OSError:
            self.skipTest("no permission to create symlinks here")
        location = usb_location("sdb", sys_root=self.root, udev_props={"ID_PATH": "pci-0000:00:14.0-usb-0:3:1.0-scsi-0:0:0:0"})
        self.assertEqual(location["port_id"], "2-3")
        self.assertEqual(location["device"], "sdb")
        self.assertTrue(location["id_path"].startswith("pci-"))
        self.assertIsNone(usb_location("sdz", sys_root=self.root))
        self.assertIsNone(usb_location("../etc", sys_root=self.root))

    def test_report_carries_the_location(self):
        report = build_disk_report(
            device={"name": "sdb", "kname": "sdb", "size": 1000 * 1000**3, "model": "T7", "children": []},
            udev_props={},
            serial_number="S1",
            candidate_type="usb",
            detection_reason="usb",
            mount_lookup={},
            usb_location={"port_id": "2-3", "busnum": 2, "devpath": "3"},
        )
        self.assertEqual(report["usb_location"]["port_id"], "2-3")


class CurrentLocationTests(TestCase):
    def test_found_by_serial(self):
        with patch("agent.main.resolve_disk", return_value=({"name": "sdc", "kname": "sdc", "serial": "S1"}, [])), patch(
            "agent.main.usb_location", return_value={"port_id": "1-5"}
        ), patch("agent.main.load_udev_properties", return_value={}):
            result = disk_usb_location_result("S1", "sdb")
        self.assertEqual((result["found"], result["device"], result["usb_location"]["port_id"]), (True, "sdc", "1-5"))

    def test_unplugged(self):
        with patch("agent.main.resolve_disk", side_effect=FileNotFoundError("gone")):
            result = disk_usb_location_result("S1", "sdb")
        self.assertFalse(result["found"])

    def test_route_is_host_only_and_validates_device(self):
        for role, expected in (("pbs", 404), ("host", 200)):
            settings = AgentSettings(server_token="t", role=role)
            server.app.dependency_overrides[server.get_settings] = lambda settings=settings: settings
            try:
                with patch("agent.server.disk_usb_location_result", return_value={"ok": True, "found": False}):
                    response = TestClient(server.app).post(
                        "/disk/usb-location", json={"serial": "S1", "device": "sdb"}, headers={"X-Agent-Token": "t"}
                    )
                    bad = TestClient(server.app).post(
                        "/disk/usb-location", json={"device": "../sda"}, headers={"X-Agent-Token": "t"}
                    )
            finally:
                server.app.dependency_overrides.clear()
            self.assertEqual(response.status_code, expected)
            if role == "host":
                self.assertEqual(bad.status_code, 422)
