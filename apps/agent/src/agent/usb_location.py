"""Physical USB location of a block device (host role), read from sysfs.

/sys/block/<dev>/device points into the device tree, e.g.

  /sys/devices/pci0000:00/0000:00:14.0/usb2/2-3/2-3:1.0/host6/target6:0:0/6:0:0:0

Walking up, the first directory holding `busnum` + `devpath` is the USB
device the disk sits behind ("2-3": bus 2, port path 3 — "2-1.4" behind a
hub). That is exactly QEMU's `host=<busnum>-<port path>` passthrough syntax,
so the app no longer has to guess the device from `/hardware/usb`, where USB
serials are often missing (QEMU disks, many USB enclosures).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

SYS_ROOT = Path("/sys")
_DEVPATH_RE = re.compile(r"^\d+(?:\.\d+)*$")


def _read(path: Path) -> str | None:
    try:
        value = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    return value or None


def usb_location_from_device_path(device_path: Path, *, stop_at: Path = SYS_ROOT) -> dict[str, Any] | None:
    """First USB device (busnum + devpath, not a root hub) above `device_path`."""
    for candidate in (device_path, *device_path.parents):
        if candidate == stop_at or candidate == candidate.parent:
            break
        busnum = _read(candidate / "busnum")
        devpath = _read(candidate / "devpath")
        if not busnum or not devpath or not busnum.isdigit():
            continue
        if devpath == "0" or not _DEVPATH_RE.match(devpath):  # root hub ("usbN")
            continue
        return {
            "busnum": int(busnum),
            "devpath": devpath,
            "port_id": f"{int(busnum)}-{devpath}",
            "vendor_id": _read(candidate / "idVendor"),
            "product_id": _read(candidate / "idProduct"),
            "serial": _read(candidate / "serial"),
            "manufacturer": _read(candidate / "manufacturer"),
            "product": _read(candidate / "product"),
        }
    return None


def usb_location(
    device_name: str,
    *,
    sys_root: Path = SYS_ROOT,
    udev_props: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    """USB location of /dev/<device_name>, None if it is not behind USB."""
    if not device_name or "/" in device_name:
        return None
    link = sys_root / "block" / device_name / "device"
    try:
        resolved = Path(os.path.realpath(link))
    except OSError:
        return None
    if not resolved.exists():
        return None
    location = usb_location_from_device_path(resolved, stop_at=sys_root)
    if location is None:
        return None
    location["device"] = device_name
    if udev_props and udev_props.get("ID_PATH"):
        location["id_path"] = udev_props["ID_PATH"]
    return location
