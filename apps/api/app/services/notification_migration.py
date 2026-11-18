"""One-time move of the legacy ntfy notifications to a notification channel
(Phase 5j, B9).

Before: NTFY_* + NOTIFICATIONS_ENABLED + NOTIFY_ON_* operator settings (stored
in app_settings, imported once from a legacy .env) and the
notification_preferences row that overrode them. After: one "ntfy" channel
whose events follow the old flags. Runs at startup until done (marker row);
it reads the legacy rows raw (they are no longer SETTING_SPECS) and, for an
install whose .env was never imported, the environment. The legacy rows and
table are left in place (an older PBO keeps working after a rollback).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import parse_bool
from app.core.security import SecretDecryptionError, decrypt_value
from app.models import AppSetting, NotificationChannel, NotificationPreferences
from app.services.notification_catalog import EVENT_KEYS
from app.services.notification_channels import DEFAULT_NTFY_SERVER, write_secrets

logger = logging.getLogger(__name__)

MARKER_KEY = "meta.notifications_migrated"
LEGACY_KEYS = ("NOTIFICATIONS_ENABLED", "NTFY_BASE_URL", "NTFY_TOPIC", "NTFY_USERNAME", "NTFY_PASSWORD")

# Old flag → the new events it covered. Events without a flag (space, SMART,
# unsafe removal, stale disk) were always sent and stay on.
FLAG_EVENTS: dict[str, tuple[str, ...]] = {
    "notify_on_backup_success": ("run_success", "repair_completed"),
    "notify_on_backup_failure": ("run_failed", "run_interrupted", "verify_failed"),
    "notify_on_update_result": ("agent_update_failed",),
    "notify_on_agent_degraded": ("agent_offline", "pve_unreachable", "pbs_unreachable", "guest_agent_missing"),
    "notify_on_disk_new_detected": ("disk_new",),
    "notify_on_planned_backup_reminder": ("disk_to_plug",),
    "notify_on_planned_backup_missed": ("rotation_missed",),
}
ALWAYS_ON = ("unsafe_removal", "disk_space", "smart_degraded", "backup_stale")


def _stored(db: Session, keys: list[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for row in db.scalars(select(AppSetting).where(AppSetting.key.in_(keys))):
        if row.value is None:
            continue
        if row.key == "NTFY_PASSWORD":
            try:
                values[row.key] = decrypt_value(row.value)
            except SecretDecryptionError:
                logger.error("Legacy NTFY_PASSWORD cannot be decrypted: migrated without it")
            continue
        values[row.key] = row.value
    return values


def migrate_legacy_ntfy(db: Session, environ: Mapping[str, str]) -> NotificationChannel | None:
    """Create the channel once. Returns it, or None (nothing to migrate / already done)."""
    if db.get(AppSetting, MARKER_KEY) is not None:
        return None
    flag_keys = [f"NOTIFY_ON_{name[len('notify_on_'):].upper()}" for name in FLAG_EVENTS]
    values = {**{k: v for k, v in environ.items() if k in (*LEGACY_KEYS, *flag_keys)}, **_stored(db, [*LEGACY_KEYS, *flag_keys])}
    channel = None
    topic = (values.get("NTFY_TOPIC") or "").strip()
    if topic:
        flags = {name: parse_bool(values.get(f"NOTIFY_ON_{name[len('notify_on_'):].upper()}"), default=True) for name in FLAG_EVENTS}
        preferences = db.get(NotificationPreferences, 1)
        enabled = parse_bool(values.get("NOTIFICATIONS_ENABLED"), default=False)
        if preferences is not None:
            flags = {name: bool(getattr(preferences, name)) for name in FLAG_EVENTS}
            if preferences.notifications_enabled_override is not None:
                enabled = preferences.notifications_enabled_override
        events = {*ALWAYS_ON, *(event for name, on in flags.items() if on for event in FLAG_EVENTS[name])}
        config = {"server_url": (values.get("NTFY_BASE_URL") or DEFAULT_NTFY_SERVER).strip().rstrip("/"), "topic": topic}
        if values.get("NTFY_USERNAME"):
            config["username"] = values["NTFY_USERNAME"].strip()
        channel = NotificationChannel(
            name="ntfy",
            kind="ntfy",
            enabled=enabled,
            config=config,
            events=[event for event in EVENT_KEYS if event in events],
            min_level="info",
            digest=False,
            digest_time="08:00",
        )
        write_secrets(channel, {"password": values["NTFY_PASSWORD"]} if values.get("NTFY_PASSWORD") and config.get("username") else {})
        db.add(channel)
        logger.info("Legacy ntfy notifications migrated to a notification channel (%s)", "enabled" if enabled else "disabled")
    db.add(AppSetting(key=MARKER_KEY, value=datetime.now(UTC).isoformat()))
    db.commit()
    return channel
