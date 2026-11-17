"""B49: an ejected disk brought back without unplugging it (agent/usb_reset.py)."""

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from agent.usb_reset import ReactivateError, port_disable_path, reactivate

EXPECTED = {"vendor_id": "174c", "product_id": "55aa", "usb_serial": "MSFT30"}


class FakeBus:
    """sysfs with one USB disk on port 2-3; `block` appears after a reset."""

    def __init__(self, root: Path, *, device=True, iface_class="08", driver=None, appear_after=1):
        self.root = root
        self.devices = root / "bus" / "usb" / "devices"
        self.writes: list[tuple[str, str]] = []
        self.resets = 0
        self.appear_after = appear_after
        port = self.devices / "usb2" / "2-0:1.0" / "usb2-port3"
        port.mkdir(parents=True)
        (port / "disable").write_text("0")
        if device:
            dev = self.devices / "2-3"
            dev.mkdir(parents=True)
            for name, value in (("authorized", "1"), ("idVendor", "174c"), ("idProduct", "55aa"), ("serial", "MSFT30")):
                (dev / name).write_text(value)
            iface = self.devices / "2-3:1.0"
            iface.mkdir()
            (iface / "bInterfaceClass").write_text(iface_class)
            if driver:
                target = root / "drivers" / driver
                target.mkdir(parents=True)
                (iface / "driver").symlink_to(target)

    def sleep(self, _seconds):
        for path in (self.devices / "2-3" / "authorized", self.devices / "usb2" / "2-0:1.0" / "usb2-port3" / "disable"):
            if path.exists():
                self.writes.append((path.parent.name, path.read_text()))

    def block(self):
        last = [value for _, value in self.writes]
        self.resets = len(last)
        return "sdb" if self.resets > self.appear_after else None


def _run(bus, **kwargs):
    clock = iter(range(1000))
    return reactivate(
        "2-3", EXPECTED, is_mounted=lambda: False, block_present=bus.block, sys_root=bus.root,
        sleep=bus.sleep, clock=lambda: next(clock), wait_seconds=10, **kwargs,
    )


@unittest.skipIf(os.name == "nt", "sysfs names contain ':' (not allowed on Windows)")
class ReactivateTests(unittest.TestCase):
    def test_authorized_toggle_when_the_device_is_on_the_bus(self):
        with TemporaryDirectory() as tmp:
            bus = FakeBus(Path(tmp))
            result = _run(bus)
            self.assertEqual((result["method"], result["device"]), ("authorized", "sdb"))
            self.assertEqual((bus.devices / "2-3" / "authorized").read_text(), "1")
            self.assertIn(("2-3", "0"), bus.writes)  # driver unbound first

    def test_port_disable_when_the_device_left_the_bus(self):
        with TemporaryDirectory() as tmp:
            bus = FakeBus(Path(tmp), device=False)
            result = _run(bus)
            self.assertEqual(result["method"], "port_disable")
            self.assertEqual(port_disable_path("2-3", sys_root=bus.root).read_text(), "0")

    def test_guards(self):
        cases = [
            ({"iface_class": "03"}, EXPECTED, "not_mass_storage"),
            ({}, {**EXPECTED, "vendor_id": "0bda"}, "usb_device_mismatch"),
        ]
        for options, expected, code in cases:
            with TemporaryDirectory() as tmp:
                bus = FakeBus(Path(tmp), **options)
                with self.assertRaises(ReactivateError) as error:
                    reactivate("2-3", expected, is_mounted=lambda: False, block_present=lambda: None, sys_root=bus.root, sleep=lambda s: None)
                self.assertEqual(error.exception.code, code)
                self.assertEqual((bus.devices / "2-3" / "authorized").read_text(), "1")  # untouched
        with TemporaryDirectory() as tmp:
            bus = FakeBus(Path(tmp))
            with self.assertRaises(ReactivateError) as error:
                reactivate("2-3", EXPECTED, is_mounted=lambda: True, block_present=lambda: None, sys_root=bus.root, sleep=lambda s: None)
            self.assertEqual(error.exception.code, "disk_mounted")

    def test_never_a_device_passed_through_to_a_vm(self):
        with TemporaryDirectory() as tmp:
            bus = FakeBus(Path(tmp), driver="usbfs")
            try:
                with self.assertRaises(ReactivateError) as error:
                    reactivate("2-3", EXPECTED, is_mounted=lambda: False, block_present=lambda: None, sys_root=bus.root, sleep=lambda s: None)
            except OSError:
                self.skipTest("symlinks not available")
            self.assertEqual(error.exception.code, "claimed_by_vm")

    def test_the_disk_must_come_back(self):
        with TemporaryDirectory() as tmp:
            bus = FakeBus(Path(tmp), appear_after=99)
            with self.assertRaises(ReactivateError) as error:
                _run(bus)
            self.assertEqual(error.exception.code, "block_device_missing")

    def test_already_there_does_nothing(self):
        with TemporaryDirectory() as tmp:
            bus = FakeBus(Path(tmp))
            result = reactivate("2-3", EXPECTED, is_mounted=lambda: False, block_present=lambda: "sdb", sys_root=bus.root)
            self.assertIsNone(result["method"])


class PortPathTests(unittest.TestCase):
    def test_port_paths(self):
        root = Path("/sys")
        self.assertEqual(port_disable_path("2-3", sys_root=root).as_posix(), "/sys/bus/usb/devices/usb2/2-0:1.0/usb2-port3/disable")
        self.assertEqual(port_disable_path("2-1.4", sys_root=root).as_posix(), "/sys/bus/usb/devices/2-1/2-1:1.0/2-1-port4/disable")
        with self.assertRaises(ReactivateError):
            port_disable_path("../2-3", sys_root=root)


if __name__ == "__main__":
    unittest.main()
