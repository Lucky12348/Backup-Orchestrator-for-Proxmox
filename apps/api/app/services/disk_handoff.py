from dataclasses import dataclass
from datetime import datetime, timedelta
from time import sleep
from typing import Any, Callable

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import ExternalDisk
from app.services.disk_identity import (
    canonical_serial_number,
    disk_matches_serial,
    serial_aliases,
)
from app.services.host_agent import (
    HostAgentError,
    HostAgentResult,
    get_host_agent_client,
    get_pbs_agent_client,
)
from app.services.proxmox_client import ProxmoxClient


@dataclass(frozen=True)
class DiskHandoffStatus:
    disk_id: int
    serial_number: str
    handoff_status: str
    proxmox_usb_mapping: str | None
    pbs_handoff_slot: str | None
    pbs_visible: bool
    pbs_device_path: str | None
    message: str


ProgressCallback = Callable[[str, str, str | None], None]


USB_DEVICE_NOT_FOUND = "usb_device_not_found"
USB_DEVICE_NOT_FOUND_MESSAGE = "Disk not found in the Proxmox USB device list."


class DiskHandoffError(HTTPException):
    """Handoff failure with a stable `code` (translated by the UI), a clear
    message (`detail`) and the technical detail for the run log."""

    def __init__(self, code: str, message: str, *, technical: str, status_code: int = status.HTTP_404_NOT_FOUND) -> None:
        super().__init__(status_code=status_code, detail=message)
        self.code = code
        self.technical = technical

    def __str__(self) -> str:
        return f"{self.detail} {self.technical}"


def handoff_disk_to_pbs(
    db: Session,
    disk: ExternalDisk,
    *,
    confirmation: bool,
    progress: ProgressCallback | None = None,
) -> DiskHandoffStatus:
    if not confirmation:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="USB handoff to PBS requires explicit confirmation.",
        )
    if disk.mount_path:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Disk is still mounted on the Proxmox host. Unmount it before PBS handoff.",
        )

    settings = get_settings()
    client = ProxmoxClient(settings)
    try:
        # 1. Where is the disk plugged right now (host agent, sysfs)? It may have
        #    moved to another port since the last report.
        location = current_usb_location(db, disk, progress)
        # 2. That exact port in Proxmox's USB list; serial matching and the
        #    "guess" fallback only when the location is unknown.
        device = resolve_usb_device(client.list_usb_devices(settings.pve_node_name), disk, location)
        _report(progress, "handoff_disk", f"USB device chosen by {device.get('source', 'serial')}: {device['mapping']}.")
    except DiskHandoffError as exc:
        # Clear message for the operator, technical detail (USB devices seen) in the run log.
        _report(progress, "handoff_disk", exc.technical)
        raise
    vm_config = _get_qemu_config_usb_map(settings)
    slot = disk.pbs_handoff_slot if disk.pbs_handoff_slot and vm_config.get(disk.pbs_handoff_slot) else _find_free_usb_slot(vm_config)

    disk.handoff_status = "attached_to_pbs"
    disk.proxmox_usb_mapping = None
    disk.pbs_handoff_slot = slot
    disk.pbs_visible = False
    disk.pbs_device_path = None
    db.add(disk)
    db.commit()
    db.refresh(disk)

    attempts = max(1, int(120 / 2))
    last_error: str | None = None
    for index, candidate in enumerate(_handoff_candidates(device), start=1):
        _report(progress, "handoff_disk", f"Selected USB mapping `{candidate['mapping']}` for `{disk.serial_number}`.")
        try:
            _attach_usb_candidate(client, settings, disk, slot, candidate, progress)
        except HTTPException as exc:
            last_error = str(exc.detail)
            _report(progress, "handoff_disk", last_error)
            if index < len(_handoff_candidates(device)):
                try:
                    _detach_usb_slot_via_host_agent(settings, slot)
                    _report(progress, "handoff_disk", f"Removed USB slot `{slot}` before next mapping retry.")
                except Exception as cleanup_exc:
                    _report(progress, "handoff_disk", f"Failed to remove USB slot `{slot}` before retry: {cleanup_exc}")
                continue
            break
        disk.handoff_status = "attached_to_pbs"
        disk.proxmox_usb_mapping = candidate["mapping"]
        disk.pbs_handoff_slot = slot
        disk.pbs_visible = False
        disk.pbs_device_path = None
        db.add(disk)
        db.commit()
        db.refresh(disk)
        try:
            return wait_for_pbs_disk_visibility(
                db,
                disk,
                attempts=attempts,
                delay_seconds=2.0,
                progress=progress,
            )
        except HTTPException as exc:
            last_error = str(exc.detail)
            if index >= 2 or not _has_vendor_product_mapping(device):
                break
            _report(progress, "handoff_disk", f"PBS did not see disk after `{candidate['mapping']}`. Retrying with vendor/product mapping.")
            _detach_usb_slot_via_host_agent(settings, slot)
            _report(progress, "handoff_disk", f"Removed USB slot `{slot}` before fallback retry.")
            disk.pbs_visible = False
            disk.pbs_device_path = None
            disk.handoff_status = "attached_to_pbs"
            db.add(disk)
            db.commit()

    try:
        _detach_usb_slot_via_host_agent(settings, slot)
        _report(progress, "handoff_disk", f"Removed USB slot `{slot}` after failed PBS visibility checks.")
    except Exception as exc:
        _report(progress, "handoff_disk", f"Failed to remove USB slot `{slot}` after failed handoff: {exc}")
    raise HTTPException(
        status_code=status.HTTP_504_GATEWAY_TIMEOUT,
        detail=last_error or "The disk was attached to the PBS VM, but the PBS agent could not see it yet.",
    )


def detach_disk_from_pbs(db: Session, disk: ExternalDisk) -> DiskHandoffStatus:
    settings = get_settings()
    if not disk.pbs_handoff_slot:
        return _build_status(disk, "Disk is not currently attached to the PBS VM.")

    try:
        _detach_usb_slot_via_host_agent(settings, disk.pbs_handoff_slot)
        vm_config = _get_qemu_config_usb_map(settings)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to detach USB disk `{disk.serial_number}` from the PBS VM: {exc}",
        ) from exc
    if disk.pbs_handoff_slot in vm_config:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to detach USB disk `{disk.serial_number}` from the PBS VM: slot `{disk.pbs_handoff_slot}` still exists.",
        )

    disk.handoff_status = "detected_on_proxmox"
    disk.pbs_visible = False
    disk.pbs_device_path = None
    disk.pbs_handoff_slot = None
    db.add(disk)
    db.commit()
    db.refresh(disk)
    return _build_status(disk, "Disk detached from the PBS VM.")


def wait_for_pbs_disk_visibility(
    db: Session,
    disk: ExternalDisk,
    *,
    attempts: int = 5,
    delay_seconds: float = 1.5,
    progress: ProgressCallback | None = None,
) -> DiskHandoffStatus:
    pbs_agent = get_pbs_agent_client()
    last_error: str | None = None

    for attempt in range(1, attempts + 1):
        result = None
        for identifier in _disk_inspection_identifiers(disk):
            try:
                result = pbs_agent.post("/inspect-disk", {"disk": identifier})
                _report(progress, "inspect_disk", f"PBS inspect retry {attempt}/{attempts} used identifier `{identifier}`.")
                break
            except HostAgentError as exc:
                last_error = str(exc)
        if result is None:
            _report(progress, "inspect_disk", f"PBS inspect retry {attempt}/{attempts} failed: {last_error}")
            sleep(delay_seconds)
            continue

        device_path = _extract_pbs_device_path(result.payload)
        mount_path = _extract_pbs_mount_path(result.payload)
        _report(progress, "inspect_disk", f"PBS inspect retry {attempt}/{attempts} succeeded: {device_path or disk.serial_number}.")
        disk.pbs_visible = True
        disk.pbs_device_path = device_path
        if mount_path:
            disk.pbs_mount_path = mount_path
        disk.pbs_filesystem_type = _extract_pbs_filesystem_type(result.payload) or disk.pbs_filesystem_type
        disk.handoff_status = "visible_on_pbs"
        db.add(disk)
        db.commit()
        db.refresh(disk)
        if mount_path:
            from app.services.disks import apply_filesystem_usage_from_payload
            apply_filesystem_usage_from_payload(db, disk, _extract_pbs_filesystem_usage_payload(result.payload))
        elif disk.pbs_mount_path:
            from app.services.disks import refresh_disk_filesystem_usage_from_pbs
            refresh_disk_filesystem_usage_from_pbs(db, disk)
        return _build_status(disk, f"Disk is now visible on PBS as {device_path or disk.serial_number}.")

    disk.pbs_visible = False
    disk.handoff_status = "attached_to_pbs"
    db.add(disk)
    db.commit()
    db.refresh(disk)
    raise HTTPException(
        status_code=status.HTTP_504_GATEWAY_TIMEOUT,
        detail=(
            "The disk was attached to the PBS VM, but the PBS agent could not see it yet."
            + (f" Last error: {last_error}" if last_error else "")
        ),
    )


def get_pbs_disk_visibility(db: Session, disk: ExternalDisk) -> DiskHandoffStatus:
    pbs_agent = get_pbs_agent_client()
    result = None
    last_error: str | None = None
    for identifier in _disk_inspection_identifiers(disk):
        try:
            result = pbs_agent.post("/inspect-disk", {"disk": identifier})
            break
        except HostAgentError as exc:
            last_error = str(exc)
    if result is None:
        return _build_status(disk, f"PBS visibility check failed: {last_error}")

    device_path = _extract_pbs_device_path(result.payload)
    mount_path = _extract_pbs_mount_path(result.payload)
    if device_path:
        disk.pbs_visible = True
        disk.pbs_device_path = device_path
        if mount_path:
            disk.pbs_mount_path = mount_path
        disk.pbs_filesystem_type = _extract_pbs_filesystem_type(result.payload) or disk.pbs_filesystem_type
        disk.handoff_status = "visible_on_pbs"
        db.add(disk)
        db.commit()
        db.refresh(disk)
        if mount_path:
            from app.services.disks import apply_filesystem_usage_from_payload
            apply_filesystem_usage_from_payload(db, disk, _extract_pbs_filesystem_usage_payload(result.payload))
        elif disk.pbs_mount_path:
            from app.services.disks import refresh_disk_filesystem_usage_from_pbs
            refresh_disk_filesystem_usage_from_pbs(db, disk)
        return _build_status(disk, f"Disk is visible on PBS as {device_path}.")

    disk.pbs_visible = False
    db.add(disk)
    db.commit()
    db.refresh(disk)
    return _build_status(disk, "Disk is not yet visible on PBS.")


def _disk_inspection_identifiers(disk: ExternalDisk) -> list[str]:
    identifiers: list[str] = []
    for value in [
        disk.serial_number,
        getattr(disk, "canonical_serial_number", None),
        getattr(disk, "reported_serial_number", None),
        getattr(disk, "pbs_device_path", None),
        *(getattr(disk, "serial_aliases", None) or []),
    ]:
        if isinstance(value, str) and value.strip() and value.strip() not in identifiers:
            identifiers.append(value.strip())
    for value in list(identifiers):
        for alias in serial_aliases(value):
            if alias and alias not in identifiers:
                identifiers.append(alias)
        canonical = canonical_serial_number(value)
        if canonical and canonical not in identifiers:
            identifiers.append(canonical)
    return identifiers


def _attach_usb_candidate(
    client: ProxmoxClient,
    settings,
    disk: ExternalDisk,
    slot: str,
    device: dict[str, str],
    progress: ProgressCallback | None,
) -> None:
    mapping = device["mapping"]
    usb3 = _usb3_enabled(device)
    payload = f"{slot}=host={mapping}" + (f",usb3={1 if usb3 else 0}" if usb3 is not None else "")
    _report(progress, "handoff_disk", f"Attach payload: `{payload}`.")
    api_response: Any = None
    try:
        api_response = _attach_usb_slot_via_host_agent(settings, slot, mapping, usb3)
        _report(progress, "handoff_disk", f"Host agent USB attach response: `{_agent_result_summary(api_response)}`.")
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                f"Failed to attach USB disk `{disk.serial_number}` to the PBS VM "
                f"on node `{settings.pbs_execution_vm_node}` VM `{settings.pbs_execution_vm_id}` "
                f"slot `{slot}` using Proxmox USB mapping `{mapping}`"
                f"{_selected_usb_detail(device)}: {exc}"
            ),
        ) from exc

    for attempt in range(1, 16):
        vm_config = _get_qemu_config_usb_map(settings)
        config_value = str(vm_config.get(slot) or "")
        _report(
            progress,
            "handoff_disk",
            f"VM config verification attempt {attempt}/15 for `{slot}`: `{config_value or 'missing'}`.",
        )
        if _vm_config_value_matches_mapping(config_value, mapping):
            return
        sleep(1)

    raise HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail=(
            f"USB attach failed on node `{settings.pbs_execution_vm_node}` VM `{settings.pbs_execution_vm_id}`. "
            f"Payload: `{payload}`. Host agent response: `{_agent_result_summary(api_response)}`. "
            f"VM config does not contain `{slot}=host={mapping}` after host agent qm update. "
            f"USB config: {_vm_usb_config_summary(vm_config)}"
        ),
    )


def _attach_usb_slot_via_host_agent(settings, slot: str, mapping: str, usb3: bool | None) -> HostAgentResult:
    return get_host_agent_client().post(
        "/qemu/usb/attach",
        {
            "vmid": settings.pbs_execution_vm_id,
            "slot": slot,
            "host": mapping,
            "usb3": usb3,
        },
    )


def _detach_usb_slot_via_host_agent(settings, slot: str) -> HostAgentResult:
    return get_host_agent_client().post(
        "/qemu/usb/detach",
        {
            "vmid": settings.pbs_execution_vm_id,
            "slot": slot,
        },
    )


def _get_qemu_config_usb_map(settings) -> dict[str, str]:
    result = get_host_agent_client().post("/qemu/config", {"vmid": settings.pbs_execution_vm_id})
    raw_config = _extract_qemu_config_text(result)
    return _parse_qm_usb_config(raw_config)


def _extract_qemu_config_text(result: HostAgentResult) -> str:
    config = result.payload.get("config")
    if isinstance(config, str):
        return config
    return result.stdout_log or ""


def _parse_qm_usb_config(raw_config: str) -> dict[str, str]:
    usb_config: dict[str, str] = {}
    for raw_line in raw_config.splitlines():
        if ":" not in raw_line:
            continue
        key, value = raw_line.split(":", 1)
        key = key.strip()
        if key.startswith("usb"):
            usb_config[key] = value.strip()
    return usb_config


def _agent_result_summary(result: HostAgentResult | None) -> str:
    if result is None:
        return "none"
    return f"message={result.message}; command={result.command_summary}; stdout={result.stdout_log}; stderr={result.stderr_log}"


def _vm_config_value_matches_mapping(config_value: str, mapping: str) -> bool:
    if not config_value:
        return False
    parts = {part.strip() for part in config_value.split(",") if part.strip()}
    return f"host={mapping}" in parts or mapping in parts or f"host={mapping}" in config_value or mapping in parts


def _vm_usb_config_summary(vm_config: dict[str, Any]) -> str:
    items = [f"{key}={value}" for key, value in sorted(vm_config.items()) if str(key).startswith("usb")]
    return ", ".join(items) or "none"


def _handoff_candidates(device: dict[str, str]) -> list[dict[str, str]]:
    candidates = [device]
    vendor_product_mapping = _vendor_product_mapping(device)
    if vendor_product_mapping and vendor_product_mapping != device["mapping"]:
        fallback = dict(device)
        fallback["mapping"] = vendor_product_mapping
        fallback["mapping_source"] = "vendor_product"
        candidates.append(fallback)
    return candidates


def _has_vendor_product_mapping(device: dict[str, str]) -> bool:
    return _vendor_product_mapping(device) is not None


# A location reported by the agent is trusted without re-asking for this long
# (only used when the agent cannot be asked: old agent, timeout).
USB_LOCATION_MAX_AGE = timedelta(minutes=15)
USB_LOCATION_NOT_IN_PROXMOX = "usb_location_not_in_proxmox"
DISK_NOT_PLUGGED = "disk_not_plugged"
USB_DEVICE_AMBIGUOUS = "usb_device_ambiguous"
USB_DEVICE_AMBIGUOUS_MESSAGE = (
    "Several USB devices could be this disk. Update the host agent (it reports the exact USB port), "
    "or unplug the other USB storage devices."
)


def _stored_location(disk: Any) -> dict[str, Any] | None:
    location = getattr(disk, "usb_location", None)
    return location if isinstance(location, dict) and location.get("port_id") else None


def _stored_location_is_recent(disk: Any, now: datetime) -> bool:
    seen_at = getattr(disk, "usb_location_seen_at", None)
    return bool(_stored_location(disk) and seen_at and now - seen_at <= USB_LOCATION_MAX_AGE)


def current_usb_location(
    db: Session | None,
    disk: Any,
    progress: ProgressCallback | None = None,
    *,
    agent_client: Any | None = None,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Ask the host agent where the disk is plugged NOW (it may have been
    unplugged and plugged into another port). Falls back to the last reported
    location while it is recent, then to None (serial matching)."""
    now = now or datetime.utcnow()
    stored = _stored_location(disk)
    try:
        client = agent_client or get_host_agent_client()
        payload = client.post(
            "/disk/usb-location",
            {"serial": disk.serial_number, "device": (stored or {}).get("device")},
        ).payload
    except HostAgentError as exc:
        if _stored_location_is_recent(disk, now):
            _report(progress, "handoff_disk", f"Host agent cannot confirm the USB port ({exc}); using the port reported {stored['port_id']}.")
            return stored
        _report(progress, "handoff_disk", f"Host agent cannot confirm the USB port ({exc}); matching by serial instead.")
        return None
    if not payload.get("found"):
        raise DiskHandoffError(
            DISK_NOT_PLUGGED,
            "The disk is no longer plugged into the Proxmox host.",
            technical=str(payload.get("message") or "Host agent did not find the disk."),
            status_code=status.HTTP_409_CONFLICT,
        )
    location = payload.get("usb_location")
    if not isinstance(location, dict) or not location.get("port_id"):
        _report(progress, "handoff_disk", "The host agent sees the disk but not as a USB device; matching by serial.")
        return None
    if stored and stored.get("port_id") != location.get("port_id"):
        _report(
            progress,
            "handoff_disk",
            f"Disk moved from USB port {stored['port_id']} to {location['port_id']}: using the new port.",
        )
    disk.usb_location = location
    disk.usb_location_seen_at = now
    if db is not None:
        db.add(disk)
        db.commit()
    return location


def resolve_usb_device(
    devices: list[dict[str, Any]], disk: Any, location: dict[str, Any] | None
) -> dict[str, str]:
    """Deterministic when the USB port is known: host=<busnum>-<port path>,
    checked against Proxmox's list. Serial / guess only without a location."""
    if location and location.get("port_id"):
        port_id = str(location["port_id"])
        for raw_device in devices:
            if _qemu_usb_host_mapping(raw_device) == port_id:
                return {**_build_usb_match(raw_device, port_id), "source": "usb_location"}
        raise DiskHandoffError(
            USB_LOCATION_NOT_IN_PROXMOX,
            "The disk's USB port is not in the Proxmox USB device list.",
            technical=(
                f"Host agent reports disk `{disk.serial_number}` at USB port {port_id} "
                f"(/dev/{location.get('device') or '?'}); Proxmox lists: {_summarize_usb_devices(devices)}"
            ),
        )
    return _find_matching_usb_device(devices, disk)


def _find_matching_usb_device(devices: list[dict[str, Any]], disk: ExternalDisk) -> dict[str, str]:
    strict_match = _find_strict_serial_match(devices, disk)
    if strict_match:
        return strict_match

    fallback_match = _find_safe_fallback_usb_match(devices, disk)
    if fallback_match:
        return fallback_match

    technical = (
        "No Proxmox USB passthrough candidate matched "
        f"disk serial `{disk.serial_number}`"
        f"{_disk_identity_suffix(disk)}. "
        f"Available USB devices: {_summarize_usb_devices(devices)}"
    )
    raise DiskHandoffError(USB_DEVICE_NOT_FOUND, USB_DEVICE_NOT_FOUND_MESSAGE, technical=technical)


def _find_strict_serial_match(devices: list[dict[str, Any]], disk: ExternalDisk) -> dict[str, str] | None:
    # Proxmox reports the raw USB serial ("PBOTEST-a") while the disk record
    # holds the canonical one ("PBOTESTA"): never compare them verbatim.
    model = (disk.model_name or "").strip().casefold()
    for raw_device in devices:
        device_serial = _candidate_value(raw_device, "serial", "serial-number", "serialnumber")
        if not disk_matches_serial(disk, device_serial):
            continue
        device_model = (_candidate_value(raw_device, "product", "name", "model") or "").casefold()
        if model and device_model and model not in device_model:
            continue
        mapping = _qemu_usb_host_mapping(raw_device)
        if not mapping:
            continue
        return _build_usb_match(raw_device, mapping)
    return None


def _find_safe_fallback_usb_match(devices: list[dict[str, Any]], disk: ExternalDisk) -> dict[str, str] | None:
    if (disk.candidate_type or "").strip().casefold() != "usb":
        return None

    matches: list[dict[str, Any]] = []
    for raw_device in devices:
        if _has_serial_identity(raw_device):
            continue
        if not _qemu_usb_host_mapping(raw_device):
            continue
        if _is_forbidden_usb_passthrough_candidate(raw_device):
            continue
        if _is_likely_storage_usb_device(raw_device, disk):
            matches.append(raw_device)

    if len(matches) == 1:
        mapping = _qemu_usb_host_mapping(matches[0])
        if mapping:
            return _build_usb_match(matches[0], mapping)
    if len(matches) > 1:
        technical = (
            "Ambiguous Proxmox USB passthrough candidates matched "
            f"disk serial `{disk.serial_number}`"
            f"{_disk_identity_suffix(disk)}. "
            f"Matched USB devices: {_summarize_usb_devices(matches)}"
        )
        raise DiskHandoffError(
            USB_DEVICE_AMBIGUOUS, USB_DEVICE_AMBIGUOUS_MESSAGE, technical=technical, status_code=status.HTTP_409_CONFLICT
        )
    return None


def _summarize_usb_devices(devices: list[dict[str, Any]]) -> str:
    if not devices:
        return "none"

    summaries: list[str] = []
    for device in devices:
        manufacturer = _candidate_value(device, "manufacturer") or "unknown manufacturer"
        product = _candidate_value(device, "product", "name", "model") or "unknown product"
        serial = _candidate_value(device, "serial", "serial-number", "serialnumber") or "no serial"
        busnum = _candidate_value(device, "busnum", "bus") or "unknown busnum"
        devnum = _candidate_value(device, "devnum", "device") or "unknown devnum"
        vendid = _candidate_value(device, "vendid", "vendorid", "vendor_id") or "unknown vendid"
        prodid = _candidate_value(device, "prodid", "productid", "product_id") or "unknown prodid"
        usbpath = _usb_debug_path(device) or "missing usbpath"
        port = _candidate_value(device, "port") or "unknown port"
        qemu_mapping = _qemu_usb_host_mapping(device) or "missing qemu mapping"
        usb_class = _candidate_value(device, "class", "classid", "usbclass", "usb_class") or "unknown class"
        summaries.append(
            f"{manufacturer} / {product} "
            f"(serial={serial}, busnum={busnum}, devnum={devnum}, usbpath={usbpath}, port={port}, "
            f"vendid={vendid}, prodid={prodid}, class={usb_class}, qemu_mapping={qemu_mapping})"
        )
    return "; ".join(summaries)


def _has_serial_identity(device: dict[str, Any]) -> bool:
    return bool(_candidate_value(device, "serial", "serial-number", "serialnumber"))


def _qemu_usb_host_mapping(device: dict[str, Any]) -> str | None:
    busnum = _candidate_value(device, "busnum", "bus")
    usbpath = _usb_debug_path(device)
    if busnum and usbpath:
        return f"{busnum}-{usbpath}"

    port = _candidate_value(device, "port")
    if busnum and port is not None:
        try:
            return f"{busnum}-{int(port) + 1}"
        except ValueError:
            pass

    return _vendor_product_mapping(device)


def _vendor_product_mapping(device: dict[str, Any]) -> str | None:
    vendid = _candidate_value(device, "vendid", "vendorid", "vendor_id")
    prodid = _candidate_value(device, "prodid", "productid", "product_id")
    if vendid and prodid:
        return f"{vendid}:{prodid}"
    return None


def _usb3_enabled(device: dict[str, Any]) -> bool | None:
    speed = _candidate_value(device, "speed", "speed_mbps")
    if speed is None:
        return None
    try:
        speed_value = int(float(speed))
    except ValueError:
        return None
    if speed_value >= 5000:
        return True
    if speed_value <= 480:
        return False
    return None


def _usb_debug_path(device: dict[str, Any]) -> str | None:
    return _candidate_value(device, "usbpath", "path", "busport", "id")


def _build_usb_match(device: dict[str, Any], mapping: str) -> dict[str, str]:
    result = {"mapping": mapping, "summary": _summarize_usb_devices([device])}
    for key in ("vendid", "vendorid", "vendor_id", "prodid", "productid", "product_id", "speed", "speed_mbps"):
        value = _candidate_value(device, key)
        if value:
            result[key] = value
    return result


def _selected_usb_detail(device: dict[str, str]) -> str:
    summary = device.get("summary")
    if not summary:
        return ""
    return f" ({summary})"


# USB device classes that can hold a disk: 0x00 (defined per interface, most
# USB-SATA bridges), 0x08 mass storage, 0xef (misc/composite), 0xff (vendor).
# Any other KNOWN class (HID 0x03, hub 0x09, audio, video, CDC…) is not a disk.
STORAGE_CAPABLE_USB_CLASSES = {0x00, 0x08, 0xEF, 0xFF}


def _usb_class_number(device: dict[str, Any]) -> int | None:
    raw = (_candidate_value(device, "class", "classid", "usbclass", "usb_class") or "").strip().casefold()
    if not raw:
        return None
    if raw in {"hid"}:
        return 0x03
    if raw in {"hub"}:
        return 0x09
    if raw in {"mass storage", "storage"}:
        return 0x08
    try:
        return int(raw, 16) if raw.startswith("0x") else int(raw)
    except ValueError:
        return None


def _is_forbidden_usb_passthrough_candidate(device: dict[str, Any]) -> bool:
    usb_class = _usb_class_number(device)
    if usb_class is not None and usb_class not in STORAGE_CAPABLE_USB_CLASSES:
        return True

    text = _normalized_usb_text(device)
    forbidden_terms = (
        "keyboard",
        "mouse",
        "tablet",
        "touchscreen",
        "touchpad",
        "trackpad",
        "pointer",
        "joystick",
        "gamepad",
        "yubikey",
        "token",
        "dongle",
        "modem",
        "serial converter",
        "uart",
        "ups",
        "uninterruptible",
        "power supply",
        "hub",
        "root hub",
        "host controller",
        "controller",
        "bluetooth",
        "receiver",
        "webcam",
        "camera",
        "audio",
        "headset",
        "microphone",
        "printer",
        "scanner",
        "smart card",
        "smartcard",
        "ethernet",
        "network",
        "wireless",
        "wifi",
    )
    return any(term in text for term in forbidden_terms)


def _is_likely_storage_usb_device(device: dict[str, Any], disk: ExternalDisk) -> bool:
    usb_class = (_candidate_value(device, "class", "classid", "usbclass", "usb_class") or "").casefold()
    if usb_class in {"8", "08", "0x08", "mass storage", "storage"}:
        return True

    text = _normalized_usb_text(device)
    storage_terms = (
        "storage",
        "drive",
        "disk",
        "hdd",
        "ssd",
        "flash",
        "thumb",
        "portable",
        "external",
        "backup",
        "passport",
        "elements",
        "easystore",
        "my book",
        "expansion",
        "game drive",
        "datatraveler",
        "cruzer",
        "ultra fit",
    )
    if any(term in text for term in storage_terms):
        return True

    known_storage_ids = {
        ("1058", "2630"),  # Western Digital Game Drive
    }
    vendid = (_candidate_value(device, "vendid", "vendorid", "vendor_id") or "").casefold()
    prodid = (_candidate_value(device, "prodid", "productid", "product_id") or "").casefold()
    if (vendid, prodid) in known_storage_ids:
        return True

    disk_text = " ".join(
        value.casefold()
        for value in (disk.model_name, disk.display_name)
        if isinstance(value, str) and value.strip()
    )
    if disk_text and _meaningful_shared_token(disk_text, text):
        return True

    return False


def _normalized_usb_text(device: dict[str, Any]) -> str:
    values = [
        _candidate_value(device, "manufacturer"),
        _candidate_value(device, "product", "name", "model"),
        _candidate_value(device, "vendid", "vendorid", "vendor_id"),
        _candidate_value(device, "prodid", "productid", "product_id"),
    ]
    return " ".join(value.casefold() for value in values if value)


def _meaningful_shared_token(left: str, right: str) -> bool:
    ignored = {"usb", "disk", "drive", "storage", "external", "portable"}
    left_tokens = {token for token in _split_match_tokens(left) if token not in ignored}
    right_tokens = {token for token in _split_match_tokens(right) if token not in ignored}
    return bool(left_tokens & right_tokens)


def _split_match_tokens(value: str) -> list[str]:
    return [token for token in "".join(char if char.isalnum() else " " for char in value).split() if len(token) >= 3]


def _disk_identity_suffix(disk: ExternalDisk) -> str:
    parts = []
    if disk.model_name:
        parts.append(f"model `{disk.model_name}`")
    if disk.display_name:
        parts.append(f"display `{disk.display_name}`")
    if not parts:
        return ""
    return " (" + ", ".join(parts) + ")"


def _find_free_usb_slot(vm_config: dict[str, Any]) -> str:
    for index in range(5):
        slot = f"usb{index}"
        if not vm_config.get(slot):
            return slot
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="No free USB passthrough slot is available on the PBS VM.",
    )


def _extract_pbs_device_path(payload: dict[str, Any]) -> str | None:
    disk_info = payload.get("disk")
    if isinstance(disk_info, dict):
        path = disk_info.get("path")
        if isinstance(path, str) and path.strip():
            return path.strip()
    return None


def _candidate_value(device: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = device.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, int):
            return str(value)
    return None


def _extract_pbs_mount_path(payload: dict[str, Any]) -> str | None:
    filesystem_info = payload.get("filesystem_info")
    if isinstance(filesystem_info, dict):
        mount_path = filesystem_info.get("mount_path")
        if isinstance(mount_path, str) and mount_path.strip():
            return mount_path.strip()
    return None


def _extract_pbs_filesystem_type(payload: dict[str, Any]) -> str | None:
    filesystem_info = payload.get("filesystem_info")
    if isinstance(filesystem_info, dict):
        filesystem_type = filesystem_info.get("filesystem_type")
        if isinstance(filesystem_type, str) and filesystem_type.strip():
            return filesystem_type.strip()
    return None


def _extract_pbs_filesystem_usage_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    filesystem_info = payload.get("filesystem_info")
    if isinstance(filesystem_info, dict):
        return filesystem_info
    return None


def _build_status(disk: ExternalDisk, message: str) -> DiskHandoffStatus:
    return DiskHandoffStatus(
        disk_id=disk.id,
        serial_number=disk.serial_number,
        handoff_status=disk.handoff_status or "detected_on_proxmox",
        proxmox_usb_mapping=disk.proxmox_usb_mapping,
        pbs_handoff_slot=disk.pbs_handoff_slot,
        pbs_visible=disk.pbs_visible,
        pbs_device_path=disk.pbs_device_path,
        message=message,
    )


def _report(progress: ProgressCallback | None, step: str, message: str) -> None:
    if progress is not None:
        progress(step, message, message)
