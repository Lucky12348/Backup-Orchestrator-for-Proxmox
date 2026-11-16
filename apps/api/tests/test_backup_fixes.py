"""First real backup test fixes: serial matching, handoff error, readiness,
session cookie."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.models.backup_run import BackupRunStatus
from app.models.external_backup_run import ExternalBackupMode, ExternalBackupRun
from app.models.external_disk import ExternalDisk
from app.services import backup_readiness
from app.services.disk_handoff import (
    USB_DEVICE_NOT_FOUND,
    DiskHandoffError,
    _find_matching_usb_device,
    handoff_disk_to_pbs,
)
from app.services.disk_identity import disk_matches_serial
from app.services.pbs_guards import match_pbs_disk
from app.services.settings_service import update_settings

ADMIN = {"username": "admin", "password": "correct horse battery"}


# --- 1. Serial matching -----------------------------------------------------------


def _disk(**overrides):
    values = {
        "serial_number": "PBOTESTA",
        "canonical_serial_number": "PBOTESTA",
        "reported_serial_number": None,
        "serial_aliases": [],
        "model_name": "USB Disk",
        "display_name": "USB Disk",
        "candidate_type": "usb",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.parametrize(
    ("stored", "seen"),
    [
        ("PBOTESTA", "PBOTEST-a"),  # dash + lower case (the pve-test case)
        ("PBOTESTA", "pbotesta"),
        ("PBOTESTA", " PBO TEST A "),  # spaces
        ("PBOTESTA", "PBO_TEST_A"),
        ("WXD2DA1L1E7C", "WD-WXD2DA1L1E7C"),  # vendor prefix
        ("WD-WXD2DA1L1E7C", "WXD2DA1L1E7C"),
    ],
)
def test_disk_matches_serial_variants(stored, seen):
    assert disk_matches_serial(_disk(serial_number=stored, canonical_serial_number=None), seen)


def test_disk_matches_serial_uses_reported_serial_and_aliases():
    assert disk_matches_serial(_disk(serial_number="X1", canonical_serial_number=None, reported_serial_number="PBOTEST-a"), "pbotesta")
    assert disk_matches_serial(_disk(serial_number="X1", canonical_serial_number=None, serial_aliases=["ALIAS9"]), "alias-9")
    assert not disk_matches_serial(_disk(), "PBOTESTB")
    assert not disk_matches_serial(_disk(), "")
    assert not disk_matches_serial(_disk(), None)


def test_proxmox_usb_serial_with_dash_matches_the_canonical_disk():
    device = {"serial": "PBOTEST-a", "product": "USB Disk", "usbpath": "2", "busnum": 1, "devnum": 5}
    other = {"serial": "OTHER-1", "product": "USB Disk", "usbpath": "3", "busnum": 1, "devnum": 6}
    result = _find_matching_usb_device([other, device], _disk())
    assert result["mapping"] == "1-2"


def test_wrong_serial_is_still_refused():
    device = {"serial": "PBOTEST-b", "product": "USB Disk", "usbpath": "2", "busnum": 1, "devnum": 5}
    with pytest.raises(DiskHandoffError):
        _find_matching_usb_device([device], _disk())


def test_pbs_disk_lookup_ignores_case_and_dashes():
    pbs_disks = [{"name": "sdb", "serial": "PBOTEST-a", "devpath": "/dev/sdb"}, {"name": "sdc", "serial": "OTHER"}]
    assert match_pbs_disk(pbs_disks, ["PBOTESTA"])["name"] == "sdb"
    assert match_pbs_disk(pbs_disks, ["sdc"])["name"] == "sdc"
    assert match_pbs_disk(pbs_disks, ["NOPE"]) is None


def test_planning_event_serial_finds_the_disk(db):
    from app.api.routes.planning import _find_disk_by_event_serial
    from app.services.planning_scheduler import _disk_matches_event_serial

    disk = ExternalDisk(
        serial_number="PBOTESTA", display_name="d", capacity_gb=1000, source="agent", reported_serial_number="PBOTEST-a"
    )
    db.add(disk)
    db.commit()
    assert _find_disk_by_event_serial(db, "pbotest-a").id == disk.id
    assert _disk_matches_event_serial(disk, "PBO TEST A")


# --- 2. Handoff error: clear message + technical detail in the run log ------------------------


def test_handoff_keeps_a_clear_message_and_logs_the_usb_devices(monkeypatch):
    seen = [{"serial": "SOMETHING-ELSE", "product": "Keyboard", "usbpath": "1", "busnum": 1, "devnum": 2}]

    class FakeClient:
        def __init__(self, settings):
            pass

        def list_usb_devices(self, node):
            return seen

    monkeypatch.setattr("app.services.disk_handoff.ProxmoxClient", FakeClient)
    monkeypatch.setattr(
        "app.services.disk_handoff.get_settings", lambda: SimpleNamespace(pve_node_name="pve", pbs_execution_vm_id=110)
    )
    monkeypatch.setattr("app.services.disk_handoff.current_usb_location", lambda db, disk, progress=None: None)
    logged = []
    disk = _disk(mount_path=None)
    with pytest.raises(DiskHandoffError) as info:
        handoff_disk_to_pbs(None, disk, confirmation=True, progress=lambda step, message, line: logged.append((step, message)))
    assert info.value.detail == "Disk not found in the Proxmox USB device list."
    assert info.value.code == USB_DEVICE_NOT_FOUND
    assert "disk not connected" not in str(info.value.detail)
    assert any(step == "handoff_disk" and "SOMETHING-ELSE" in message and "Available USB devices" in message for step, message in logged)


def test_run_failure_keeps_code_and_technical_detail(session_factory, monkeypatch):
    from app.services import external_backups

    with session_factory() as setup_db:
        disk = ExternalDisk(serial_number="PBOTESTA", display_name="d", capacity_gb=1000, source="agent", trusted=True, connected=True)
        setup_db.add(disk)
        setup_db.commit()
        run = ExternalBackupRun(
            disk_id=disk.id, status=BackupRunStatus.PENDING, started_at=external_backups.datetime.utcnow(),
            created_at=external_backups.datetime.utcnow(), target_path="/x", datastore_name="main",
            mode=ExternalBackupMode.DEDICATED,
        )
        setup_db.add(run)
        setup_db.commit()
        run_id = run.id
    error = DiskHandoffError(USB_DEVICE_NOT_FOUND, "Disk not found in the Proxmox USB device list.", technical="Available USB devices: kbd")

    def boom(*args, **kwargs):
        raise error

    monkeypatch.setattr(external_backups, "handoff_disk_to_pbs", boom)
    monkeypatch.setattr(external_backups, "SessionLocal", session_factory)
    monkeypatch.setattr(external_backups, "get_external_backup_execution_service", lambda: None)
    for name in ("notify_backup_failure", "notify_backup_success"):
        if hasattr(external_backups, name):
            monkeypatch.setattr(external_backups, name, lambda *a, **k: None)
    external_backups.execute_external_backup_run(run_id)
    with session_factory() as db:
        run = db.get(ExternalBackupRun, run_id)
    assert run.status == BackupRunStatus.FAILED
    assert run.current_step == USB_DEVICE_NOT_FOUND
    assert run.message == "Disk not found in the Proxmox USB device list."
    # Item 3 (5b): ONE coded ERR line (translated by the UI), no duplicate.
    from app.services import run_log

    errors = [entry for entry in run_log.build_entries(run.stdout_log, run.stderr_log) if entry.level == "err"]
    assert [entry.code for entry in errors] == ["handoff_usb_not_found"]
    assert (run.stderr_log or "").count("Disk not found in the Proxmox USB device list.") == 1


def test_unplugged_disk_failure_is_a_single_translated_error_line(session_factory, monkeypatch):
    """The real handoff path: the agent says the disk is gone → one ERR line
    `handoff_disk_not_plugged`, never the agent's English text three times."""
    from app.services import disk_handoff, external_backups, run_log

    with session_factory() as setup_db:
        disk = ExternalDisk(serial_number="PBOTESTA", display_name="Disk", capacity_gb=500, connected=True, trusted=True,
                            dedicated_backup_disk=True, source="agent", active=True)
        setup_db.add(disk)
        setup_db.commit()
        now = external_backups.datetime.utcnow()
        run = ExternalBackupRun(disk_id=disk.id, status=BackupRunStatus.PENDING, started_at=now,
                                target_path="/mnt", datastore_name="store", mode=ExternalBackupMode.DEDICATED,
                                current_step="starting", created_at=now)
        setup_db.add(run)
        setup_db.commit()
        run_id = run.id

    class _Agent:
        def post(self, path, payload):
            return SimpleNamespace(payload={"found": False, "message": "Disk not found on the Proxmox host."})

    monkeypatch.setattr(disk_handoff, "get_host_agent_client", lambda: _Agent())
    monkeypatch.setattr(disk_handoff, "get_settings", lambda: SimpleNamespace(pve_node_name="pve", pbs_execution_vm_id=100,
                                                                               pve_api_url="https://pve", host_agent_base_url="https://agent"))
    monkeypatch.setattr(disk_handoff, "ProxmoxClient", lambda *a, **k: SimpleNamespace(list_usb_devices=lambda node: []))
    monkeypatch.setattr(external_backups, "SessionLocal", session_factory)
    monkeypatch.setattr(external_backups, "get_external_backup_execution_service", lambda: None)
    for name in ("notify_backup_failure", "notify_backup_success"):
        monkeypatch.setattr(external_backups, name, lambda *a, **k: None)
    external_backups.execute_external_backup_run(run_id)
    with session_factory() as db:
        run = db.get(ExternalBackupRun, run_id)
    assert run.status == BackupRunStatus.FAILED
    assert run.current_step == "disk_not_plugged"
    entries = run_log.build_entries(run.stdout_log, run.stderr_log)
    errors = [entry for entry in entries if entry.level == "err"]
    assert [entry.code for entry in errors] == ["handoff_disk_not_plugged"]
    assert "Disk not found on the Proxmox host" not in (run.stdout_log or "") + (run.stderr_log or "")


# --- 3. Readiness -------------------------------------------------------------------------------


class FakeVersion:
    def __init__(self, capabilities=None, error=None):
        self.capabilities = capabilities
        self.error = error

    def get_version(self):
        if self.error:
            raise self.error
        return {"ok": True, "capabilities": self.capabilities or []}


HOST_OK = FakeVersion(["version-endpoint", "qemu-usb-attach", "qemu-usb-detach"])
PBS_API_OK = FakeVersion(["version-endpoint"])


@pytest.fixture(autouse=True)
def _fresh_cache():
    backup_readiness.reset_version_cache()
    yield
    backup_readiness.reset_version_cache()


def _configure(db, mode="api"):
    update_settings(
        db,
        {
            "PBS_MODE": mode,
            "PVE_API_URL": "https://10.0.0.1:8006/api2/json",
            "PVE_API_TOKEN_ID": "pbo@pve!pbo",
            "PVE_API_TOKEN_SECRET": "s",
            "PBS_EXECUTION_VM_ID": 110,
            "HOST_AGENT_BASE_URL": "https://10.0.0.1:8090",
            "HOST_AGENT_TOKEN": "t",
            "PBS_API_URL": "https://10.0.0.5:8007/api2/json",
            "PBS_TOKEN_ID": "pbo@pbs!pbo",
            "PBS_TOKEN_SECRET": "s",
            "PBS_DATASTORE": "main",
        },
    )


def _ready_disk(db, **overrides):
    values = {"serial_number": "R1", "display_name": "d", "capacity_gb": 1000, "source": "agent", "trusted": True, "connected": True}
    values.update(overrides)
    disk = ExternalDisk(**values)
    db.add(disk)
    db.commit()
    return disk


def _codes(result):
    return [reason["code"] for reason in result["reasons"]]


def test_api_mode_ready_without_any_pbs_agent(db):
    _configure(db, "api")
    result = backup_readiness.external_backup_readiness(db, _ready_disk(db), host_client=HOST_OK, pbs_client=PBS_API_OK)
    assert {key: result[key] for key in ("disk_id", "ready", "pbs_mode", "reasons", "warnings")} == {
        "disk_id": result["disk_id"], "ready": True, "pbs_mode": "api", "reasons": [], "warnings": [],
    }


def test_reasons_are_specific(db):
    _configure(db, "api")
    disk = _ready_disk(db, connected=False, trusted=False, capacity_gb=16)
    db.add(ExternalBackupRun(
        disk_id=disk.id, status=BackupRunStatus.RUNNING, started_at=disk_started(), created_at=disk_started(),
        target_path="/x", datastore_name="main", mode=ExternalBackupMode.DEDICATED,
    ))
    db.commit()
    result = backup_readiness.external_backup_readiness(
        db, disk, host_client=FakeVersion(["version-endpoint"]), pbs_client=FakeVersion(error=RuntimeError("down"))
    )
    assert result["ready"] is False
    assert _codes(result) == [
        "disk_too_small", "disk_not_connected", "disk_not_approved", "run_active", "host_agent_outdated", "pbs_unreachable",
    ]
    outdated = next(reason for reason in result["reasons"] if reason["code"] == "host_agent_outdated")
    assert outdated["missing"] == ["qemu-usb-attach", "qemu-usb-detach"]


def disk_started():
    from datetime import datetime

    return datetime.utcnow()


def test_agent_mode_requires_the_pbs_agent(db):
    _configure(db, "agent")
    result = backup_readiness.external_backup_readiness(db, _ready_disk(db), host_client=HOST_OK)
    assert _codes(result) == ["pbs_agent_missing"]


def test_missing_configuration(db):
    result = backup_readiness.external_backup_readiness(db, _ready_disk(db), host_client=HOST_OK, pbs_client=PBS_API_OK)
    assert {"pve_not_configured", "host_agent_missing", "pbs_agent_missing"} <= set(_codes(result))  # fallback mode: agent


def test_versions_are_cached(db):
    _configure(db, "api")
    calls = []

    class Counting(FakeVersion):
        def get_version(self):
            calls.append(1)
            return super().get_version()

    host = Counting(["version-endpoint", "qemu-usb-attach", "qemu-usb-detach"])
    for _ in range(3):
        backup_readiness.external_backup_readiness(db, _ready_disk(db, serial_number=f"S{len(calls)}{_}"), host_client=host, pbs_client=PBS_API_OK)
    assert len(calls) == 1


def test_readiness_route(client, monkeypatch):
    assert client.post("/api/v1/setup/admin", json=ADMIN).status_code == 201
    assert client.get("/api/v1/external-backups/readiness/999").status_code == 404
    monkeypatch.setattr(
        backup_readiness, "external_backup_readiness", lambda db, disk, **kw: {"disk_id": disk.id, "ready": True, "pbs_mode": "api", "reasons": []}
    )


def test_readiness_route_requires_a_session(client):
    assert client.get("/api/v1/external-backups/readiness/1").status_code == 401


def test_system_version_reports_the_pbs_mode(client, monkeypatch):
    assert client.post("/api/v1/setup/admin", json=ADMIN).status_code == 201

    class Broken:
        def get_version(self):
            raise ValueError("unexpected backend failure")

    monkeypatch.setattr("app.api.routes.system.get_host_agent_client", lambda: Broken())
    monkeypatch.setattr("app.api.routes.system.get_pbs_agent_client", lambda: Broken())
    body = client.get("/api/v1/system/version").json()
    assert "pbs_mode" in body
    assert body["pbs_agent"]["ok"] is False and "unexpected backend failure" in body["pbs_agent"]["message"]


# --- 4. Session cookie ----------------------------------------------------------------------------


def test_session_cookie_is_lax_on_root_path(client):
    response = client.post("/api/v1/setup/admin", json=ADMIN)
    cookie = response.headers["set-cookie"].lower()
    assert "samesite=lax" in cookie and "path=/" in cookie and "httponly" in cookie


def test_api_url_opened_in_a_tab_is_authenticated(client):
    """A top-level navigation (e.g. /api/v1/system/version typed or opened
    from another app) sends the Lax cookie: it must not say Not authenticated."""
    assert client.post("/api/v1/setup/admin", json=ADMIN).status_code == 201
    headers = {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"}
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 200


def test_cross_site_writes_are_refused(client):
    assert client.post("/api/v1/setup/admin", json=ADMIN).status_code == 201
    cross = client.put("/api/v1/auth/me/preferences", json={"language": "fr"}, headers={"Sec-Fetch-Site": "cross-site"})
    assert cross.status_code == 403
    same = client.put("/api/v1/auth/me/preferences", json={"language": "fr"}, headers={"Sec-Fetch-Site": "same-origin"})
    assert same.status_code == 200


def test_readiness_codes_are_translated_by_the_ui():
    """Every readiness code must have a text in the web translations."""
    import re
    from pathlib import Path

    source = (Path(backup_readiness.__file__)).read_text(encoding="utf-8")
    codes = set(re.findall(r'_reason\(\s*"([a-z_]+)"', source)) | {"disk_zero_size", "disk_too_small"}
    i18n = (Path(__file__).resolve().parents[2] / "web" / "src" / "i18n.ts").read_text(encoding="utf-8")
    missing = sorted(code for code in codes if i18n.count(f"{code}:") < 2)  # en + fr
    assert missing == []


def test_handoff_error_is_an_http_exception():
    error = DiskHandoffError(USB_DEVICE_NOT_FOUND, "msg", technical="tech")
    assert isinstance(error, HTTPException) and error.status_code == 404
