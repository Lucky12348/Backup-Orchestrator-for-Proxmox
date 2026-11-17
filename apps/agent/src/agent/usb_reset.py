"""Re-enumerate an ejected USB disk without unplugging it (B49, host role).

After an eject the disk is spun down / powered off: its USB device may stay on
the bus while its block device is gone (nothing usable), or the device may
have been logically removed from the bus. Two documented kernel interfaces
bring it back, tried in this order:

1. `authorized` of the USB device (Documentation/usb/authorization.rst):
   writing 0 unbinds every driver of the device, writing 1 probes it again —
   as if it was just plugged in. Used when the device is still on the bus.
2. `disable` of the hub port (Documentation/ABI/testing/sysfs-bus-usb,
   `<hub>-port<N>/disable`): 1 then 0 turns the port off and on, the device
   re-enumerates. Used when the device is no longer on the bus.

Guards: only the USB device recorded for this disk (port + vendor/product,
USB serial when known), only a mass-storage device, never one claimed by a VM
(usbfs = QEMU passthrough), never one with a mounted partition. Then the
block device with the disk's serial must appear within the timeout.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

SYS_ROOT = Path("/sys")
_PORT_RE = re.compile(r"^(?P<bus>\d+)-(?P<path>\d+(?:\.\d+)*)$")
WAIT_SECONDS = 30.0


class ReactivateError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="ascii").strip()
    except OSError:
        return None


def _write(path: Path, value: str) -> None:
    path.write_text(value, encoding="ascii")


def port_disable_path(port_id: str, *, sys_root: Path = SYS_ROOT) -> Path:
    """`2-3` → /sys/bus/usb/devices/usb2/2-0:1.0/usb2-port3/disable;
    `2-1.4` → /sys/bus/usb/devices/2-1/2-1:1.0/2-1-port4/disable."""
    match = _PORT_RE.match(port_id)
    if not match:
        raise ReactivateError("bad_port", f"Invalid USB port `{port_id}`.")
    bus, chain = match.group("bus"), match.group("path").split(".")
    port = chain[-1]
    if len(chain) == 1:
        hub, interface, prefix = f"usb{bus}", f"{bus}-0:1.0", f"usb{bus}"
    else:
        hub = f"{bus}-{'.'.join(chain[:-1])}"
        interface, prefix = f"{hub}:1.0", hub
    return sys_root / "bus" / "usb" / "devices" / hub / interface / f"{prefix}-port{port}" / "disable"


def _interfaces(device: Path) -> list[Path]:
    return sorted(path for path in device.parent.glob(f"{device.name}:*") if path.is_dir())


def _driver(interface: Path) -> str | None:
    link = interface / "driver"
    try:
        return link.resolve().name if link.exists() else None
    except OSError:
        return None


def check_device(device: Path, expected: dict[str, Any]) -> None:
    for key, attribute in (("vendor_id", "idVendor"), ("product_id", "idProduct"), ("usb_serial", "serial")):
        want = expected.get(key)
        if want and (_read(device / attribute) or "").lower() != str(want).lower():
            raise ReactivateError("usb_device_mismatch", f"The USB device on port {device.name} is not this disk ({attribute} differs).")
    interfaces = _interfaces(device)
    if interfaces:
        classes = {(_read(interface / "bInterfaceClass") or "").lower() for interface in interfaces}
        if "08" not in classes:
            raise ReactivateError("not_mass_storage", f"The USB device on port {device.name} is not a storage device.")
        if any(_driver(interface) == "usbfs" for interface in interfaces):
            raise ReactivateError("claimed_by_vm", f"The USB device on port {device.name} is passed through to a VM.")


def reactivate(
    port_id: str,
    expected: dict[str, Any],
    *,
    is_mounted: Callable[[], bool],
    block_present: Callable[[], str | None],
    sys_root: Path = SYS_ROOT,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    wait_seconds: float = WAIT_SECONDS,
) -> dict[str, Any]:
    if not _PORT_RE.match(port_id or ""):
        raise ReactivateError("bad_port", f"Invalid USB port `{port_id}`.")
    current = block_present()
    if current:
        return {"ok": True, "success": True, "method": None, "device": current, "message": f"/dev/{current} is already there."}
    if is_mounted():
        raise ReactivateError("disk_mounted", "A partition of this disk is mounted: refusing to reset it.")
    device = sys_root / "bus" / "usb" / "devices" / port_id
    if (device / "authorized").exists():
        check_device(device, expected)
        _write(device / "authorized", "0")
        sleep(1.0)
        _write(device / "authorized", "1")
        method = "authorized"
    else:
        disable = port_disable_path(port_id, sys_root=sys_root)
        if not disable.exists():
            raise ReactivateError("no_reset_interface", f"No USB device on port {port_id} and no port `disable` control: unplug the disk and plug it back in.")
        _write(disable, "1")
        sleep(1.0)
        _write(disable, "0")
        method = "port_disable"
    deadline = clock() + wait_seconds
    while clock() < deadline:
        sleep(1.0)
        found = block_present()
        if found:
            return {"ok": True, "success": True, "method": method, "device": found, "message": f"Disk back as /dev/{found} ({method})."}
    raise ReactivateError("block_device_missing", f"The disk did not come back after the USB reset ({method}): unplug it and plug it back in.")
