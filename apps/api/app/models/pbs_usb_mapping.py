from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class PbsUsbMapping(Base):
    """A `usbN: host=…` line PBO put on the PBS VM for a disk (Phase 5f.2).

    Open while `released_at` is null. A mapping left behind would hand any
    device later plugged into that port to PBS, so PBO removes the ones of
    disks no longer in use (services/passthrough_watch.py). Only a VM config
    line still equal to `host_value` is ever removed: never someone else's.

    Schema owned by Alembic (see app/db/migrations).
    """

    __tablename__ = "pbs_usb_mappings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    vmid: Mapped[int] = mapped_column(Integer, nullable=False)
    slot: Mapped[str] = mapped_column(String(16), nullable=False)
    host_value: Mapped[str] = mapped_column(String(128), nullable=False)
    disk_id: Mapped[int | None] = mapped_column(ForeignKey("external_disks.id", ondelete="SET NULL"), nullable=True)
    disk_serial: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    release_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
