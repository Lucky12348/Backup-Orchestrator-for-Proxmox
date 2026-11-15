from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SetupProgress(Base):
    """First-run wizard state (single row, id=1): the step the operator is on
    and non-secret results shown again after a reload (node, versions,
    fingerprints, chosen datastore...). Secrets live in `app_settings` only.

    Schema owned by Alembic (see app/db/migrations).
    """

    __tablename__ = "setup_progress"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    current_step: Mapped[str] = mapped_column(String(32), nullable=False, default="agent")
    data: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )
