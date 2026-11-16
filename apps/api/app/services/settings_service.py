"""Operator settings stored in the `app_settings` table.

* `SETTING_SPECS` is the registry of every database-backed setting: its key
  (also the legacy environment variable name), the matching `Settings` field,
  its type, whether it is secret, and its UI group.
* Secret values are encrypted (see app/core/security.py) and never returned by
  the API: reads expose only whether they are set.
* `import_settings_from_env()` runs once, on the first start after upgrade, to
  copy an existing `.env` into the database. After that the environment is no
  longer read for these keys.
* `activate_settings_store()` makes `app.core.config.get_settings()` overlay
  stored values on top of the environment bootstrap values.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, fields, replace
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, invalidate_settings_cache, parse_bool, set_settings_overlay_loader
from app.core.security import SecretDecryptionError, decrypt_value, encrypt_value
from app.models.app_setting import AppSetting

logger = logging.getLogger(__name__)

ENV_IMPORT_MARKER_KEY = "meta.env_import_done"
MAX_AUTH_TOKEN_EXPIRE_MINUTES = 180


@dataclass(frozen=True)
class SettingSpec:
    key: str
    field: str
    type: type
    group: str
    secret: bool = False
    choices: tuple[str, ...] | None = None


def _spec(key: str, type_: type, group: str, *, secret: bool = False, choices: tuple[str, ...] | None = None) -> SettingSpec:
    return SettingSpec(key=key, field=key.lower(), type=type_, group=group, secret=secret, choices=choices)


PBS_MODE_AGENT = "agent"
PBS_MODE_API = "api"


_NOTIFY_FLAGS = (
    "NOTIFY_ON_BACKUP_SUCCESS",
    "NOTIFY_ON_BACKUP_FAILURE",
    "NOTIFY_ON_DISK_EJECT_READY",
    "NOTIFY_ON_UPDATE_RESULT",
    "NOTIFY_ON_AGENT_DEGRADED",
    "NOTIFY_ON_LOW_COVERAGE",
    "NOTIFY_ON_DISK_NEW_DETECTED",
    "NOTIFY_ON_DISK_KNOWN_DETECTED",
    "NOTIFY_ON_PLANNED_DISK_DETECTED",
    "NOTIFY_ON_PLANNED_BACKUP_REMINDER",
    "NOTIFY_ON_PLANNED_BACKUP_STARTED",
    "NOTIFY_ON_PLANNED_CONFIRMATION_REQUIRED",
    "NOTIFY_ON_PLANNED_BACKUP_MISSED",
)

SETTING_SPECS: tuple[SettingSpec, ...] = (
    _spec("AUTH_TOKEN_EXPIRE_MINUTES", int, "auth"),
    _spec("TIMEZONE", str, "general"),
    # Proxmox VE
    _spec("PVE_API_URL", str, "proxmox"),
    _spec("PVE_API_TOKEN_ID", str, "proxmox"),
    _spec("PVE_API_TOKEN_SECRET", str, "proxmox", secret=True),
    _spec("PVE_VERIFY_SSL", bool, "proxmox"),
    _spec("PVE_FINGERPRINT", str, "proxmox"),
    _spec("PVE_NODE_NAME", str, "proxmox"),
    _spec("PBS_EXECUTION_VM_ID", int, "proxmox"),
    _spec("PBS_EXECUTION_VM_NODE", str, "proxmox"),
    # Proxmox Backup Server
    _spec("PBS_API_URL", str, "pbs"),
    _spec("PBS_TOKEN_ID", str, "pbs"),
    _spec("PBS_TOKEN_SECRET", str, "pbs", secret=True),
    _spec("PBS_VERIFY_SSL", bool, "pbs"),
    _spec("PBS_FINGERPRINT", str, "pbs"),
    _spec("PBS_DATASTORE", str, "pbs"),
    _spec("PBS_MODE", str, "pbs", choices=(PBS_MODE_API, PBS_MODE_AGENT)),
    _spec("PBS_TASK_TIMEOUT_SECONDS", float, "pbs"),
    _spec("PBS_EJECT_TIMEOUT_SECONDS", float, "pbs"),
    # Agents
    _spec("HOST_AGENT_BASE_URL", str, "agents"),
    _spec("HOST_AGENT_TOKEN", str, "agents", secret=True),
    _spec("HOST_AGENT_TIMEOUT_SECONDS", float, "agents"),
    _spec("PBS_AGENT_BASE_URL", str, "agents"),
    _spec("PBS_AGENT_TOKEN", str, "agents", secret=True),
    _spec("PBS_AGENT_TIMEOUT_SECONDS", float, "agents"),
    _spec("EXTERNAL_BACKUP_CALLBACK_BASE_URL", str, "agents"),
    _spec("AGENT_STALE_AFTER_MINUTES", int, "agents"),
    SettingSpec("AGENT_LOOP_DATASTORE_SIZE_GB", "external_loop_datastore_size_gb", int, "agents"),
    # Notifications (ntfy)
    _spec("NOTIFICATIONS_ENABLED", bool, "notifications"),
    _spec("NTFY_BASE_URL", str, "notifications"),
    _spec("NTFY_TOPIC", str, "notifications"),
    _spec("NTFY_USERNAME", str, "notifications"),
    _spec("NTFY_PASSWORD", str, "notifications", secret=True),
    *(_spec(flag, bool, "notifications") for flag in _NOTIFY_FLAGS),
    _spec("LOW_COVERAGE_THRESHOLD_PERCENT", float, "notifications"),
    _spec("DISK_DETECTION_NOTIFY_COOLDOWN_SECONDS", int, "notifications"),
    # Inventory sync
    _spec("AUTO_SYNC_ENABLED", bool, "sync"),
    _spec("PROXMOX_SYNC_INTERVAL_SECONDS", int, "sync"),
    _spec("PBS_SYNC_INTERVAL_SECONDS", int, "sync"),
)

SPECS_BY_KEY: dict[str, SettingSpec] = {spec.key: spec for spec in SETTING_SPECS}
_SETTINGS_FIELDS = {f.name for f in fields(Settings)}
assert all(spec.field in _SETTINGS_FIELDS for spec in SETTING_SPECS), "SETTING_SPECS out of sync with Settings"


class SettingValidationError(ValueError):
    pass


def _default_for(spec: SettingSpec) -> Any:
    return Settings.__dataclass_fields__[spec.field].default


def coerce_value(spec: SettingSpec, raw: Any) -> Any:
    """Validate and convert an API/env value to the spec type."""
    try:
        if spec.type is bool:
            if isinstance(raw, bool):
                return raw
            if isinstance(raw, str) and raw.strip().lower() in {"1", "true", "yes", "on", "0", "false", "no", "off"}:
                return parse_bool(raw)
            raise ValueError("expected a boolean")
        if spec.type is int:
            if isinstance(raw, bool):
                raise ValueError("expected an integer")
            value = int(str(raw).strip())
        elif spec.type is float:
            if isinstance(raw, bool):
                raise ValueError("expected a number")
            value = float(str(raw).strip())
        else:
            if not isinstance(raw, str):
                raise ValueError("expected a string")
            value = raw if spec.secret else raw.strip()
    except (TypeError, ValueError) as exc:
        raise SettingValidationError(f"{spec.key}: {exc}") from exc

    if spec.choices is not None and value not in spec.choices:
        raise SettingValidationError(f"{spec.key}: must be one of {', '.join(spec.choices)}")
    if spec.type in (int, float) and value < 0:
        raise SettingValidationError(f"{spec.key}: must not be negative")
    if spec.key == "AUTH_TOKEN_EXPIRE_MINUTES" and not 5 <= value <= MAX_AUTH_TOKEN_EXPIRE_MINUTES:
        raise SettingValidationError(f"{spec.key}: must be between 5 and {MAX_AUTH_TOKEN_EXPIRE_MINUTES}")
    return value


def _serialize(spec: SettingSpec, value: Any) -> str:
    if spec.type is bool:
        return "true" if value else "false"
    text = str(value)
    return encrypt_value(text) if spec.secret else text


def _deserialize(spec: SettingSpec, stored: str) -> Any:
    raw = decrypt_value(stored) if spec.secret else stored
    return coerce_value(spec, raw)


def load_overrides(db: Session) -> dict[str, Any]:
    """Stored values as `Settings` field overrides. Unreadable rows are skipped."""
    overrides: dict[str, Any] = {}
    rows = db.scalars(select(AppSetting).where(AppSetting.key.in_(list(SPECS_BY_KEY)))).all()
    for row in rows:
        spec = SPECS_BY_KEY[row.key]
        if row.value is None:
            continue
        try:
            overrides[spec.field] = _deserialize(spec, row.value)
        except (SecretDecryptionError, SettingValidationError) as exc:
            logger.error("Ignoring stored setting %s: %s", spec.key, exc)
    return overrides


def update_settings(db: Session, values: Mapping[str, Any], *, commit: bool = True) -> list[str]:
    """Validate and persist values keyed by setting key. `None` resets to default.

    Validation happens for every key before anything is written.
    """
    unknown = sorted(set(values) - set(SPECS_BY_KEY))
    if unknown:
        raise SettingValidationError(f"Unknown setting(s): {', '.join(unknown)}")

    prepared: dict[str, str | None] = {}
    for key, raw in values.items():
        spec = SPECS_BY_KEY[key]
        if key == "TIMEZONE" and raw is not None:
            from app.core.timezones import InvalidTimezoneError, validate_timezone

            try:
                raw = validate_timezone(str(raw))
            except InvalidTimezoneError as exc:
                raise SettingValidationError(str(exc)) from exc
        prepared[key] = None if raw is None else _serialize(spec, coerce_value(spec, raw))

    for key, stored in prepared.items():
        row = db.get(AppSetting, key)
        if stored is None:
            if row is not None:
                db.delete(row)
            continue
        if row is None:
            db.add(AppSetting(key=key, value=stored, is_secret=SPECS_BY_KEY[key].secret))
        else:
            row.value = stored
            row.is_secret = SPECS_BY_KEY[key].secret
    if commit:
        db.commit()
        invalidate_settings_cache()
    return sorted(prepared)


def stored_secret(db: Session, key: str) -> str | None:
    """Decrypted secret, for server-side use only (never returned to clients)."""
    spec = SPECS_BY_KEY[key]
    if not spec.secret:
        raise ValueError(f"{key} is not a secret setting")
    row = db.get(AppSetting, key)
    if row is None or row.value is None:
        return None
    try:
        return decrypt_value(row.value)
    except SecretDecryptionError:
        return None


def load_all_overrides(db: Session) -> dict[str, Any]:
    """Stored settings + the connection of registered agents (which win over
    the static HOST_AGENT_* / PBS_AGENT_* values)."""
    from app.services.agent_registry import connection_overrides

    overrides = load_overrides(db)
    overrides.update(connection_overrides(db))
    return overrides


def effective_settings(db: Session, base: Settings | None = None) -> Settings:
    return replace(base or Settings(), **load_all_overrides(db))


def settings_view(db: Session, groups: Iterable[str] | None = None) -> list[dict[str, Any]]:
    """Client-safe listing: secrets are reported as set/unset only."""
    wanted = set(groups) if groups is not None else None
    overrides = load_overrides(db)
    items = []
    for spec in SETTING_SPECS:
        if wanted is not None and spec.group not in wanted:
            continue
        stored = spec.field in overrides
        items.append(
            {
                "key": spec.key,
                "group": spec.group,
                "type": spec.type.__name__,
                "secret": spec.secret,
                # Secrets are write-only: only `stored` tells whether one exists.
                "value": None if spec.secret else overrides.get(spec.field, _default_for(spec)),
                "stored": stored,
            }
        )
    return items


def import_settings_from_env(db: Session, environ: Mapping[str, str]) -> list[str]:
    """One-time copy of a pre-existing `.env` into the database.

    Runs only if the import marker is absent; empty or invalid values are
    skipped (and logged) so a bad legacy value cannot block startup.
    """
    if db.get(AppSetting, ENV_IMPORT_MARKER_KEY) is not None:
        return []

    values: dict[str, Any] = {}
    for spec in SETTING_SPECS:
        raw = environ.get(spec.key)
        if raw is None or raw.strip() == "":
            continue
        if spec.key == "AUTH_TOKEN_EXPIRE_MINUTES":
            # Legacy behaviour: oversized values were silently capped.
            try:
                raw = str(max(5, min(int(raw.strip()), MAX_AUTH_TOKEN_EXPIRE_MINUTES)))
            except ValueError:
                pass
        try:
            values[spec.key] = coerce_value(spec, raw)
        except SettingValidationError as exc:
            logger.warning("Not importing %s from environment: %s", spec.key, exc)
    if "PBS_EXECUTION_VM_NODE" not in values and "PVE_NODE_NAME" in values:
        # Historical fallback: the PBS VM node defaulted to PVE_NODE_NAME.
        values["PBS_EXECUTION_VM_NODE"] = values["PVE_NODE_NAME"]

    imported = update_settings(db, values, commit=False)
    db.add(AppSetting(key=ENV_IMPORT_MARKER_KEY, value=",".join(imported), is_secret=False))
    db.commit()
    invalidate_settings_cache()
    if imported:
        logger.info("Imported %d setting(s) from the environment into the database: %s", len(imported), ", ".join(imported))
    return imported


def ensure_pbs_mode(db: Session) -> str:
    """Persist PBS_MODE once: "api" for a fresh install, "agent" for an upgrade.

    An install counts as an upgrade when a legacy .env was imported, a PBS
    agent is configured, or a disk was already prepared by the agent (those
    datastores are not removable datastores and still need the agent path).
    """
    row = db.get(AppSetting, "PBS_MODE")
    if row is not None and row.value:
        return row.value
    marker = db.get(AppSetting, ENV_IMPORT_MARKER_KEY)
    legacy = bool(marker is not None and marker.value)
    legacy = legacy or db.get(AppSetting, "PBS_AGENT_BASE_URL") is not None
    if not legacy:
        from app.models.external_disk import ExternalDisk

        legacy = db.scalar(
            select(ExternalDisk.id).where(ExternalDisk.prepared_as_pbs_datastore.is_(True)).limit(1)
        ) is not None
    mode = PBS_MODE_AGENT if legacy else PBS_MODE_API
    update_settings(db, {"PBS_MODE": mode})
    logger.info("PBS mode initialised to %r (%s install).", mode, "upgraded" if legacy else "fresh")
    return mode


def activate_settings_store(session_factory) -> None:  # noqa: ANN001
    """Make get_settings() overlay stored values (called at startup)."""

    def _load() -> dict[str, Any]:
        with session_factory() as db:
            return load_all_overrides(db)

    set_settings_overlay_loader(_load)
