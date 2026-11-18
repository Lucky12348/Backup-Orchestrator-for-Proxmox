import pytest

from app.core import config
from app.core.security import ENCRYPTED_PREFIX, SecretDecryptionError, decrypt_value, encrypt_value, validate_secret_key
from app.models.app_setting import AppSetting
from app.services.settings_service import (
    ENV_IMPORT_MARKER_KEY,
    SETTING_SPECS,
    SettingValidationError,
    activate_settings_store,
    effective_settings,
    import_settings_from_env,
    load_overrides,
    settings_view,
    stored_secret,
    update_settings,
)


def test_secret_roundtrip_and_key_binding(secret_key):
    stored = encrypt_value("s3cr3t")
    assert stored.startswith(ENCRYPTED_PREFIX)
    assert "s3cr3t" not in stored
    assert decrypt_value(stored) == "s3cr3t"
    with pytest.raises(SecretDecryptionError):
        decrypt_value(stored, secret_key="another-secret-key-0123456789abcdef")


def test_validate_secret_key_rejects_default_and_short_keys():
    with pytest.raises(RuntimeError, match="insecure default"):
        validate_secret_key("change-me-in-production-use-a-long-random-string")
    with pytest.raises(RuntimeError, match="too short"):
        validate_secret_key("short")
    validate_secret_key("x" * 32)


def test_registry_covers_settings_fields():
    fields = {spec.field for spec in SETTING_SPECS}
    assert {"pve_api_token_secret", "pbs_datastore", "host_agent_token", "public_url", "run_history_mode"} <= fields
    secrets = {spec.key for spec in SETTING_SPECS if spec.secret}
    # Phase 5j: notification secrets live with their channel, not in app_settings.
    assert secrets == {"PVE_API_TOKEN_SECRET", "PBS_TOKEN_SECRET", "HOST_AGENT_TOKEN", "PBS_AGENT_TOKEN"}


def test_update_encrypts_secrets_and_types_values(db, secret_key):
    update_settings(db, {"PBS_TOKEN_SECRET": "tok-secret", "PBS_EXECUTION_VM_ID": "105", "PVE_VERIFY_SSL": "true"})

    row = db.get(AppSetting, "PBS_TOKEN_SECRET")
    assert row.is_secret and "tok-secret" not in row.value
    assert stored_secret(db, "PBS_TOKEN_SECRET") == "tok-secret"

    settings = effective_settings(db)
    assert settings.pbs_token_secret == "tok-secret"
    assert settings.pbs_execution_vm_id == 105
    assert settings.pve_verify_ssl is True


def test_update_validates_everything_before_writing(db, secret_key):
    with pytest.raises(SettingValidationError, match="PBS_EXECUTION_VM_ID"):
        update_settings(db, {"PBS_DATASTORE": "store", "PBS_EXECUTION_VM_ID": "abc"})
    with pytest.raises(SettingValidationError, match="Unknown"):
        update_settings(db, {"NOT_A_SETTING": "x"})
    with pytest.raises(SettingValidationError, match="AUTH_TOKEN_EXPIRE_MINUTES"):
        update_settings(db, {"AUTH_TOKEN_EXPIRE_MINUTES": 10_000})
    assert db.get(AppSetting, "PBS_DATASTORE") is None


def test_none_resets_to_default(db, secret_key):
    update_settings(db, {"PVE_NODE_NAME": "pve2"})
    assert effective_settings(db).pve_node_name == "pve2"
    update_settings(db, {"PVE_NODE_NAME": None})
    assert effective_settings(db).pve_node_name == "proxmox"


def test_view_never_exposes_secrets(db, secret_key):
    update_settings(db, {"HOST_AGENT_TOKEN": "agent-token-value", "HOST_AGENT_BASE_URL": "http://pve:8090"})
    items = {item["key"]: item for item in settings_view(db)}
    assert items["HOST_AGENT_TOKEN"] == {
        "key": "HOST_AGENT_TOKEN",
        "group": "agents",
        "type": "str",
        "secret": True,
        "value": None,
        "stored": True,
    }
    assert items["HOST_AGENT_BASE_URL"]["value"] == "http://pve:8090"
    assert items["PBS_AGENT_TOKEN"]["stored"] is False
    assert "agent-token-value" not in repr(settings_view(db))


def test_unreadable_secret_is_skipped_not_fatal(db, secret_key, monkeypatch):
    update_settings(db, {"PBS_TOKEN_SECRET": "pw", "PBS_DATASTORE": "main"})
    monkeypatch.setenv("AUTH_SECRET_KEY", "rotated-secret-key-0123456789abcdef0123")
    overrides = load_overrides(db)
    assert "pbs_token_secret" not in overrides
    assert overrides["pbs_datastore"] == "main"


def test_env_import_runs_once(db, secret_key):
    environ = {
        "PVE_API_URL": "https://pve:8006/api2/json",
        "PVE_API_TOKEN_SECRET": "pve-secret",
        "PVE_NODE_NAME": "pve1",
        "PBS_EXECUTION_VM_ID": "not-a-number",  # skipped, not fatal
        "NTFY_TOPIC": "",  # empty values are ignored
        "AUTH_TOKEN_EXPIRE_MINUTES": "9999",  # clamped
    }
    imported = import_settings_from_env(db, environ)
    assert set(imported) == {
        "PVE_API_URL",
        "PVE_API_TOKEN_SECRET",
        "PVE_NODE_NAME",
        "PBS_EXECUTION_VM_NODE",
        "AUTH_TOKEN_EXPIRE_MINUTES",
    }
    settings = effective_settings(db)
    assert settings.pbs_execution_vm_node == "pve1"
    assert settings.auth_token_expire_minutes == 180
    assert db.get(AppSetting, ENV_IMPORT_MARKER_KEY) is not None

    # A later .env change is not re-imported: the database is the source of truth.
    assert import_settings_from_env(db, {"PVE_NODE_NAME": "other"}) == []
    assert effective_settings(db).pve_node_name == "pve1"


def test_get_settings_overlays_store_and_invalidates(session_factory, secret_key):
    try:
        activate_settings_store(session_factory)
        assert config.get_settings().pbs_datastore == ""
        with session_factory() as db:
            update_settings(db, {"PBS_DATASTORE": "offsite"})
        assert config.get_settings().pbs_datastore == "offsite"
        # Bootstrap values still come from the environment base.
        assert config.get_settings().database_url == config.get_env_settings().database_url
    finally:
        config.set_settings_overlay_loader(None)
    assert config.get_settings() is config.get_env_settings()


def test_get_settings_degrades_when_store_unavailable():
    def _broken():
        raise RuntimeError("database down")

    try:
        config.set_settings_overlay_loader(_broken)
        assert config.get_settings() is config.get_env_settings()
    finally:
        config.set_settings_overlay_loader(None)
