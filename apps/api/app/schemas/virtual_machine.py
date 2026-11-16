from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models import VMType
from app.schemas.base import UTCDateTimeModel


class VirtualMachineRead(UTCDateTimeModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    vm_type: VMType
    critical: bool
    size_gb: int
    enabled: bool
    source: str
    external_id: str | None
    node_name: str | None
    runtime_status: str | None
    last_seen_at: datetime | None
    last_backup_at: datetime | None
    ignored: bool = False
    ignore_reason: str | None = None
    # Last successful external copy of this VM/CT (services/external_copies.py).
    last_external_copy_at: datetime | None = None
    # Phase 5f: rotation disks holding a copy (cached disk contents), newest first:
    # [{"disk_id", "disk_name", "color", "newest", "versions", "cached_at"}].
    external_copies: list[dict] = []


class VirtualMachineUpdate(BaseModel):
    critical: bool | None = None
    enabled: bool | None = None
    size_gb: int | None = Field(default=None, ge=0)
