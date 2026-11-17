from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AgentUpdate(Base):
    """One self-update of a registered agent (services/agent_updates.py).

    status: running | success | up_to_date | rolled_back | failed | too_old | busy
    trigger: auto (heartbeat with an older build) | manual (Integrations) |
    update_command (end of the LXC `update`).

    Schema owned by Alembic (0014_agent_updates).
    """

    __tablename__ = "agent_updates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    trigger: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    from_build: Mapped[str | None] = mapped_column(String(64), nullable=True)
    to_build: Mapped[str] = mapped_column(String(64), nullable=False)
    from_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    to_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
