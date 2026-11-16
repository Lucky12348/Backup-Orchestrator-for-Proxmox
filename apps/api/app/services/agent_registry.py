"""One-line agent installation: pairing codes, registration, agent tokens.

Flow (docs/INSTALLATION.md, "Agent hôte"):

1. The admin asks for a pairing code (`POST /api/v1/agents/pairing`): 8
   characters, single use, valid 15 minutes. Only its SHA-256 is stored.
2. On the Proxmox host, `install.sh` (served by the app) installs the agent,
   generates its self-signed certificate, then calls `POST
   /api/v1/agents/register` with the code, its role, hostname, base URL and
   certificate fingerprint.
3. The app answers with two distinct tokens:
   * the **agent token** (app → agent, sent as X-Agent-Token): stored
     encrypted, because the app has to send it;
   * the **callback token** (agent → app: heartbeat, disk reports, run logs):
     only its SHA-256 is stored.
4. From then on every app → agent call pins the registered fingerprint, and
   `get_settings()` exposes the registered agent instead of the static
   HOST_AGENT_* / PBS_AGENT_* settings (`connection_overrides`).

Static tokens from a legacy `.env` stay accepted for the roles that have no
registered agent.
"""

from __future__ import annotations

import hashlib
import ipaddress
import logging
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import invalidate_settings_cache
from app.core.security import SecretDecryptionError, decrypt_value, encrypt_value
from app.core.tls import FingerprintFormatError, normalize_fingerprint
from app.models.registered_agent import AgentPairingCode, RegisteredAgent

logger = logging.getLogger(__name__)

AGENT_ROLES = ("host", "pbs")
PAIRING_CODE_TTL = timedelta(minutes=15)
# No 0/O, 1/I/L: the code is read on one screen and typed on another.
PAIRING_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
PAIRING_CODE_LENGTH = 8
REGISTER_MAX_FAILURES = 10
REGISTER_FAILURE_WINDOW_SECONDS = 15 * 60
_HOSTNAME_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,251}[A-Za-z0-9])?$")


class PairingError(ValueError):
    """Invalid, expired or already used pairing code (deliberately vague)."""


class RegistrationValidationError(ValueError):
    pass


class TooManyAttemptsError(RuntimeError):
    pass


@dataclass(frozen=True)
class PairingCode:
    code: str
    expires_at: datetime


@dataclass(frozen=True)
class Registration:
    agent: RegisteredAgent
    agent_token: str
    callback_token: str


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_aware(value: datetime) -> datetime:
    # SQLite returns naive datetimes even for timezone=True columns.
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalize_pairing_code(code: str) -> str:
    return re.sub(r"[\s-]", "", code or "").upper()


def format_pairing_code(raw: str) -> str:
    return f"{raw[:4]}-{raw[4:]}"


# --- Failed registration attempts (brute-force brake) ------------------------

_failures_lock = Lock()
_failures: dict[str, list[float]] = {}


def _prune(key: str, now: float) -> list[float]:
    attempts = [t for t in _failures.get(key, []) if now - t < REGISTER_FAILURE_WINDOW_SECONDS]
    _failures[key] = attempts
    return attempts


def check_register_rate(client_key: str, now: float) -> None:
    with _failures_lock:
        if len(_prune(client_key, now)) >= REGISTER_MAX_FAILURES:
            raise TooManyAttemptsError("Too many failed registration attempts, retry later.")


def record_register_failure(client_key: str, now: float) -> None:
    with _failures_lock:
        _prune(client_key, now).append(now)


def reset_register_failures() -> None:
    with _failures_lock:
        _failures.clear()


# --- Pairing codes -------------------------------------------------------------


def create_pairing_code(db: Session, *, now: datetime | None = None) -> PairingCode:
    now = now or _utcnow()
    raw = "".join(secrets.choice(PAIRING_ALPHABET) for _ in range(PAIRING_CODE_LENGTH))
    expires_at = now + PAIRING_CODE_TTL
    # Housekeeping: expired or used codes are useless.
    for stale in db.scalars(select(AgentPairingCode).where(AgentPairingCode.expires_at < now - timedelta(days=1))):
        db.delete(stale)
    db.add(AgentPairingCode(code_hash=_sha256(raw), created_at=now, expires_at=expires_at))
    db.commit()
    return PairingCode(code=format_pairing_code(raw), expires_at=expires_at)


# --- Registration ----------------------------------------------------------------


def validate_registration(role: str, hostname: str, base_url: str, fingerprint: str) -> tuple[str, str, str, str]:
    role = (role or "").strip().lower()
    if role not in AGENT_ROLES:
        raise RegistrationValidationError(f"role must be one of {', '.join(AGENT_ROLES)}.")
    hostname = (hostname or "").strip()
    if not _HOSTNAME_RE.match(hostname):
        raise RegistrationValidationError("hostname is not a valid host name.")
    base_url = (base_url or "").strip().rstrip("/")
    parts = urlsplit(base_url)
    if parts.scheme != "https" or not parts.hostname or parts.path or parts.query or parts.fragment:
        raise RegistrationValidationError("base_url must look like https://<address>:<port>.")
    try:
        port = parts.port
    except ValueError as exc:
        raise RegistrationValidationError("base_url has an invalid port.") from exc
    if port is None:
        raise RegistrationValidationError("base_url must include the agent port.")
    try:
        unspecified = ipaddress.ip_address(parts.hostname).is_unspecified
    except ValueError:
        unspecified = False  # a DNS name
    if unspecified:
        raise RegistrationValidationError("base_url must use the agent's real address, not 0.0.0.0.")
    try:
        fingerprint = normalize_fingerprint(fingerprint)
    except FingerprintFormatError as exc:
        raise RegistrationValidationError(str(exc)) from exc
    if not fingerprint:
        raise RegistrationValidationError("fingerprint (SHA-256 of the agent certificate) is required.")
    return role, hostname, base_url, fingerprint


def register_agent(
    db: Session,
    *,
    code: str,
    role: str,
    hostname: str,
    base_url: str,
    fingerprint: str,
    now: datetime | None = None,
) -> Registration:
    """Consume the pairing code and create the agent. Previous agents of the
    same role are revoked: the app drives one host agent and one PBS agent."""
    now = now or _utcnow()
    role, hostname, base_url, fingerprint = validate_registration(role, hostname, base_url, fingerprint)
    normalized = normalize_pairing_code(code)
    if len(normalized) != PAIRING_CODE_LENGTH:
        raise PairingError("Invalid or expired pairing code.")
    pairing = db.scalar(select(AgentPairingCode).where(AgentPairingCode.code_hash == _sha256(normalized)))
    if pairing is None or pairing.used_at is not None or _as_aware(pairing.expires_at) <= now:
        raise PairingError("Invalid or expired pairing code.")

    agent_token = secrets.token_urlsafe(32)
    callback_token = secrets.token_urlsafe(32)
    for previous in db.scalars(
        select(RegisteredAgent).where(RegisteredAgent.role == role, RegisteredAgent.revoked_at.is_(None))
    ):
        previous.revoked_at = now
        logger.info("Agent %s (%s, %s) replaced by a new registration.", previous.id, previous.role, previous.hostname)
    agent = RegisteredAgent(
        role=role,
        hostname=hostname,
        base_url=base_url,
        fingerprint=fingerprint,
        token_encrypted=encrypt_value(agent_token),
        callback_token_hash=_sha256(callback_token),
        created_at=now,
    )
    db.add(agent)
    db.flush()
    pairing.used_at = now
    pairing.agent_id = agent.id
    db.commit()
    invalidate_settings_cache()
    logger.info("Agent registered: id=%s role=%s hostname=%s url=%s", agent.id, role, hostname, base_url)
    return Registration(agent=agent, agent_token=agent_token, callback_token=callback_token)


def list_agents(db: Session) -> list[RegisteredAgent]:
    return list(db.scalars(select(RegisteredAgent).order_by(RegisteredAgent.id.desc())))


def revoke_agent(db: Session, agent_id: int, *, now: datetime | None = None) -> RegisteredAgent | None:
    agent = db.get(RegisteredAgent, agent_id)
    if agent is None:
        return None
    if agent.revoked_at is None:
        agent.revoked_at = now or _utcnow()
        db.commit()
        invalidate_settings_cache()
    return agent


def purge_revoked_agents(db: Session) -> int:
    """Integrations page "Delete the revoked agents": their rows go, the
    active agent of each role stays."""
    revoked = list(db.scalars(select(RegisteredAgent).where(RegisteredAgent.revoked_at.is_not(None))))
    for agent in revoked:
        db.delete(agent)
    db.commit()
    return len(revoked)


def active_agent(db: Session, role: str) -> RegisteredAgent | None:
    return db.scalar(
        select(RegisteredAgent)
        .where(RegisteredAgent.role == role, RegisteredAgent.revoked_at.is_(None))
        .order_by(RegisteredAgent.id.desc())
        .limit(1)
    )


def has_active_agent(db: Session, role: str | None = None) -> bool:
    query = select(RegisteredAgent.id).where(RegisteredAgent.revoked_at.is_(None))
    if role is not None:
        query = query.where(RegisteredAgent.role == role)
    return db.scalar(query.limit(1)) is not None


def authenticate_callback(
    db: Session, token: str | None, *, roles: tuple[str, ...] = AGENT_ROLES, now: datetime | None = None
) -> RegisteredAgent | None:
    """Active agent owning this callback token (and marks it as seen)."""
    if not token:
        return None
    agent = db.scalar(
        select(RegisteredAgent).where(
            RegisteredAgent.callback_token_hash == _sha256(token), RegisteredAgent.revoked_at.is_(None)
        )
    )
    if agent is None or agent.role not in roles:
        return None
    agent.last_seen_at = now or _utcnow()
    db.commit()
    return agent


def connection_overrides(db: Session) -> dict[str, Any]:
    """`Settings` overrides for the active registered agents (settings overlay)."""
    overrides: dict[str, Any] = {}
    for role in AGENT_ROLES:
        agent = active_agent(db, role)
        if agent is None:
            continue
        try:
            token = decrypt_value(agent.token_encrypted)
        except SecretDecryptionError as exc:
            logger.error("Registered %s agent %s unusable: %s", role, agent.id, exc)
            continue
        overrides[f"{role}_agent_base_url"] = agent.base_url
        overrides[f"{role}_agent_token"] = token
        overrides[f"{role}_agent_fingerprint"] = agent.fingerprint
    return overrides
