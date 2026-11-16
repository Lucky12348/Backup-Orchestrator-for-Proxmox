from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.base import UTCDateTimeModel

class AgentHeartbeatCreate(BaseModel):
    hostname: str = Field(max_length=255)
    agent_version: str = Field(max_length=64)
    observed_at: datetime


class AgentUsbLocation(BaseModel):
    """Physical USB port of the disk, read by the host agent in sysfs."""

    busnum: int = Field(ge=0, le=999)
    devpath: str = Field(pattern=r"^\d+(?:\.\d+)*$", max_length=64)
    port_id: str = Field(pattern=r"^\d+-\d+(?:\.\d+)*$", max_length=70)
    device: str | None = Field(default=None, max_length=32)
    vendor_id: str | None = Field(default=None, max_length=16)
    product_id: str | None = Field(default=None, max_length=16)
    serial: str | None = Field(default=None, max_length=255)
    manufacturer: str | None = Field(default=None, max_length=255)
    product: str | None = Field(default=None, max_length=255)
    id_path: str | None = Field(default=None, max_length=255)


class AgentDiskReportItem(BaseModel):
    serial_number: str = Field(max_length=255)
    display_name: str = Field(max_length=255)
    model_name: str | None = Field(default=None, max_length=255)
    capacity_gb: int = Field(ge=0)
    filesystem_total_gb: int | None = Field(default=None, ge=0)
    filesystem_used_gb: int | None = Field(default=None, ge=0)
    filesystem_free_gb: int | None = Field(default=None, ge=0)
    filesystem_type: str | None = Field(default=None, max_length=64)
    mount_path: str | None = Field(default=None, max_length=255)
    detection_reason: str | None = Field(default=None, max_length=255)
    candidate_type: str | None = Field(default=None, max_length=64)
    trusted: bool = False
    connected: bool
    usb_location: AgentUsbLocation | None = None


class AgentHotplugEvent(BaseModel):
    """A USB disk udev event seen by the agent since its previous report."""

    action: Literal["added", "removed"]
    serial: str = Field(max_length=255)
    model: str | None = Field(default=None, max_length=255)


class AgentDiskReportCreate(BaseModel):
    hostname: str = Field(max_length=255)
    observed_at: datetime
    disks: list[AgentDiskReportItem]
    # What triggered the report: "timer" (periodic) or "udev" (hot-plug).
    trigger: Literal["timer", "udev", "manual"] | None = None
    hotplug: list[AgentHotplugEvent] = Field(default_factory=list, max_length=64)


class AgentHeartbeatRead(UTCDateTimeModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    hostname: str
    agent_version: str
    observed_at: datetime


class AgentStatusRead(UTCDateTimeModel):
    connected: bool
    hostname: str | None
    last_heartbeat_at: datetime | None
    last_report_at: datetime | None
    status: str
    stale_after_minutes: int
    last_seen_age_seconds: int | None
