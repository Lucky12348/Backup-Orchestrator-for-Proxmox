from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RegisteredAgent(Base):
    """Agent installed with a pairing code (`POST /api/v1/agents/register`).

    * `token_encrypted`: app → agent token (X-Agent-Token sent by the app),
      Fernet-encrypted like secret settings: the app must be able to send it.
    * `callback_token_hash`: agent → app token, only its SHA-256 is kept.
    * `fingerprint`: SHA-256 of the agent TLS certificate, pinned on every call.

    Schema owned by Alembic (see app/db/migrations).
    """

    __tablename__ = "agents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    hostname: Mapped[str] = mapped_column(String(253), nullable=False)
    base_url: Mapped[str] = mapped_column(String(512), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(95), nullable=False)
    token_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    callback_token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Announced by the agent on each heartbeat and /version (Alembic 0014): compared
    # with the build the app ships (services/agent_updates.py).
    agent_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    build_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    version_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentPairingCode(Base):
    """Single-use pairing code (only its SHA-256 is stored), valid 15 minutes."""

    __tablename__ = "agent_pairing_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    agent_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
