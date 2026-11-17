from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.base import UTCDateTimeModel


class ExternalDiskRead(UTCDateTimeModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    serial_number: str
    reported_serial_number: str | None
    reported_display_name: str | None
    reported_model_name: str | None
    reported_mount_path: str | None
    canonical_serial_number: str | None
    serial_aliases: list[str] | None
    display_name: str
    capacity_gb: int
    connected: bool
    dedicated_backup_disk: bool
    allow_existing_data: bool
    preferred_root_path: str | None
    notes: str | None
    filesystem_type: str | None
    model_name: str | None
    mount_path: str | None
    filesystem_total_gb: int | None
    filesystem_used_gb: int | None
    filesystem_free_gb: int | None
    last_seen_at: datetime | None
    detection_reason: str | None
    candidate_type: str | None
    trusted: bool
    usable_capacity_gb: int | None
    reserved_capacity_gb: int
    planning_notes: str | None
    source: str
    active: bool
    handoff_status: str | None
    proxmox_usb_mapping: str | None
    pbs_handoff_slot: str | None
    pbs_visible: bool
    pbs_device_path: str | None
    pbs_datastore_name: str | None
    pbs_mount_path: str | None
    pbs_filesystem_type: str | None
    prepared_as_pbs_datastore: bool
    operator_decision: str | None = None
    usb_location: dict | None = None
    partitions: list[dict] | None = None
    # Derived (app/services/disk_events.py): new | known | ejected | unplugged | rejected.
    status: str = "new"
    reject_reason: str | None = None
    min_size_gb: int | None = None
    # Phase 5f: profile, hardware, guards.
    color: str | None = None
    location_note: str | None = None
    photo_version: str | None = None
    kind: str = "unknown"
    rotational: bool | None = None
    transport: str | None = None
    usb_speed_mbps: int | None = None
    smart_status: str | None = None
    smart_details: dict | None = None
    smart_checked_at: datetime | None = None
    effective_retention: dict | None = None
    usage_history: list[dict] | None = None
    verify_recommended: bool | None = None
    verify_required: bool | None = None
    damaged_snapshots: list[dict] | None = None
    unsafe_removal_at: datetime | None = None
    contents_cached_at: datetime | None = None


class ExternalDiskDecision(BaseModel):
    """Disks page approval flow. `approve` / `approve_anyway` (a disk smaller
    than the minimum, Phase 5g) need the disk name typed back; `hide` keeps a
    disk out of the list until `clear`."""

    decision: Literal["approve", "approve_anyway", "ignore", "reject", "hide", "clear"]
    confirm_name: str | None = Field(default=None, max_length=255)


class DiskRetention(BaseModel):
    keep_last: int | None = Field(default=None, ge=1, le=1000)
    keep_weekly: int | None = Field(default=None, ge=1, le=1000)
    keep_monthly: int | None = Field(default=None, ge=1, le=1000)


class ExternalDiskUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=255)
    # Phase 5f
    color: Literal["slate", "blue", "green", "amber", "red", "violet"] | None = None
    location_note: str | None = Field(default=None, max_length=500)
    retention: DiskRetention | None = None
    dedicated_backup_disk: bool | None = None
    allow_existing_data: bool | None = None
    trusted: bool | None = None
    usable_capacity_gb: int | None = Field(default=None, ge=0)
    reserved_capacity_gb: int | None = Field(default=None, ge=0)
    planning_notes: str | None = None
    preferred_root_path: str | None = Field(default=None, max_length=255)
    notes: str | None = None
