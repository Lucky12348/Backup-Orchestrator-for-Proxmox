"""Application settings.

Two kinds of values live on the `Settings` dataclass:

* **Bootstrap / environment values** (database URL, secret key, app env…) are
  read from the process environment once, at import time.
* **Operator values** (Proxmox/PBS endpoints and tokens, agent URLs and tokens,
  ntfy, sync intervals…) are stored in the `app_settings` table and edited
  through the setup wizard / `/api/v1/settings`. Their dataclass defaults below
  are static: the environment is only consulted once, on first start, to
  import an existing `.env` into the database
  (see `app.services.settings_service.import_settings_from_env`).

`get_settings()` returns the environment values overlaid with the stored
operator values once the settings store has been activated at startup. Before
that (and in unit tests) it returns the environment-only base.
"""

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, replace
from functools import lru_cache
from threading import Lock
from typing import Any

logger = logging.getLogger(__name__)


def parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default

    return value.strip().lower() in {"1", "true", "yes", "on"}


def normalize_database_url(database_url: str) -> str:
    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+psycopg://", 1)

    return database_url


@dataclass(frozen=True)
class Settings:
    app_name: str = "Proxmox Backup Orchestrator API"

    # --- Bootstrap values: environment only -------------------------------
    app_env: str = os.getenv("APP_ENV", "production")
    database_url: str = normalize_database_url(
        os.getenv(
            "DATABASE_URL",
            "postgresql+psycopg://postgres:postgres@db:5432/proxmox_backup_orchestrator",
        )
    )
    # Demo data (fake VMs/disks/runs) is only created when explicitly requested.
    dev_seed: bool = parse_bool(os.getenv("DEV_SEED"), default=False)
    # Session cookie `Secure` flag. Keep true unless the UI is reached over plain
    # HTTP on a non-localhost address (browsers drop Secure cookies there).
    auth_cookie_secure: bool = parse_bool(os.getenv("AUTH_COOKIE_SECURE"), default=True)
    # Built web UI served by the API (same origin). Empty = auto-detect.
    web_dist_dir: str = os.getenv("WEB_DIST_DIR", "")
    # TLS certificate the app serves (LXC install: /etc/pbo/tls/cert.pem). Only
    # read to show its fingerprint when pairing an agent; uvicorn itself gets
    # the certificate from its command line.
    tls_cert_file: str = os.getenv("PBO_TLS_CERT_FILE", "")
    planning_scheduler_enabled: bool = parse_bool(os.getenv("PLANNING_SCHEDULER_ENABLED"), default=True)
    planning_scheduler_interval_seconds: int = int(os.getenv("PLANNING_SCHEDULER_INTERVAL_SECONDS", "60"))
    # Legacy loop-image coexistence mode, kept as an operator escape hatch.
    external_backup_legacy_coexistence_enabled: bool = parse_bool(
        os.getenv("EXTERNAL_BACKUP_LEGACY_COEXISTENCE_ENABLED"),
        default=False,
    )
    # Docker-era app maintenance agent (git pull + compose rebuild).
    maintenance_timeout_seconds: float = float(os.getenv("MAINTENANCE_TIMEOUT_SECONDS", "120"))
    app_maintenance_agent_base_url: str = os.getenv(
        "APP_MAINTENANCE_AGENT_BASE_URL",
        "http://127.0.0.1:8092",
    )
    app_maintenance_agent_token: str = os.getenv("APP_MAINTENANCE_AGENT_TOKEN", "")

    # --- Operator values: stored in `app_settings` ---------------------------
    # Keep this block in sync with SETTING_SPECS in app/services/settings_service.py.
    auth_token_expire_minutes: int = 180
    notifications_enabled: bool = False
    ntfy_base_url: str = "https://ntfy.sh"
    ntfy_topic: str = ""
    ntfy_username: str = ""
    ntfy_password: str = ""
    notify_on_backup_success: bool = True
    notify_on_backup_failure: bool = True
    notify_on_disk_eject_ready: bool = True
    notify_on_update_result: bool = True
    notify_on_agent_degraded: bool = True
    notify_on_low_coverage: bool = True
    notify_on_disk_new_detected: bool = True
    notify_on_disk_known_detected: bool = True
    notify_on_planned_disk_detected: bool = True
    notify_on_planned_backup_reminder: bool = True
    notify_on_planned_backup_started: bool = True
    notify_on_planned_confirmation_required: bool = True
    notify_on_planned_backup_missed: bool = True
    low_coverage_threshold_percent: float = 100.0
    disk_detection_notify_cooldown_seconds: int = 1800
    pve_api_url: str = ""
    pve_api_token_id: str = ""
    pve_api_token_secret: str = ""
    pve_verify_ssl: bool = False
    pve_fingerprint: str = ""
    pve_node_name: str = "proxmox"
    pbs_execution_vm_id: int = 100
    pbs_execution_vm_node: str = "proxmox"
    pbs_api_url: str = ""
    pbs_token_id: str = ""
    pbs_token_secret: str = ""
    pbs_verify_ssl: bool = False
    pbs_fingerprint: str = ""
    # Source PBS datastore name, chosen by the operator. No default name is
    # assumed: empty means "not configured" and is reported as such.
    pbs_datastore: str = ""
    # How removable-disk datastores are driven on PBS: "api" (PBS REST API,
    # PBS >= 3.3, no root agent on PBS) or "agent" (legacy PBS root agent).
    # Fresh installs store "api", upgraded installs "agent" (see
    # settings_service.ensure_pbs_mode). The static fallback stays "agent" so a
    # settings-store outage never silently switches the execution path.
    pbs_mode: str = "agent"
    pbs_task_timeout_seconds: float = 7200.0
    pbs_eject_timeout_seconds: float = 1800.0
    host_agent_base_url: str = ""
    host_agent_token: str = ""
    host_agent_timeout_seconds: float = 7200.0
    pbs_agent_base_url: str = ""
    pbs_agent_token: str = ""
    pbs_agent_timeout_seconds: float = 7200.0
    # Not settings: filled from the `agents` table when an agent registered
    # itself (app/services/agent_registry.py). Non-empty = that agent's TLS
    # certificate is pinned on every call and it has its own callback token.
    host_agent_fingerprint: str = ""
    pbs_agent_fingerprint: str = ""
    # Previous default kept on purpose (Docker service name); the PBS agent
    # must actually reach this URL, so operators set it in the settings.
    external_backup_callback_base_url: str = "http://api:8000/api/v1"
    external_loop_datastore_size_gb: int = 500
    agent_stale_after_minutes: int = 10
    auto_sync_enabled: bool = True
    proxmox_sync_interval_seconds: int = 60
    pbs_sync_interval_seconds: int = 60

    @property
    def show_seed_disks(self) -> bool:
        return self.dev_seed


@lru_cache(maxsize=1)
def get_env_settings() -> Settings:
    """Environment-only settings (no database access). Safe at import time."""
    return Settings()


_overlay_loader: Callable[[], dict[str, Any]] | None = None
_merged_settings: Settings | None = None
_merged_lock = Lock()


def set_settings_overlay_loader(loader: Callable[[], dict[str, Any]] | None) -> None:
    """Install (or remove) the function returning stored operator values."""
    global _overlay_loader
    with _merged_lock:
        _overlay_loader = loader
    invalidate_settings_cache()


def invalidate_settings_cache() -> None:
    global _merged_settings
    with _merged_lock:
        _merged_settings = None


def get_settings() -> Settings:
    global _merged_settings
    base = get_env_settings()
    with _merged_lock:
        loader = _overlay_loader
        if loader is None:
            return base
        if _merged_settings is not None:
            return _merged_settings
        try:
            overrides = loader()
        except Exception as exc:  # database unreachable: degrade, do not cache
            logger.warning("Stored settings unavailable, using defaults: %s", exc)
            return base
        _merged_settings = replace(base, **overrides)
        return _merged_settings
