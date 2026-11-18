"""PBS_MODE=api: full preparation → sync → eject flow against a fake PBS, plus guards."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.core.config import Settings
from app.models import BackupRunStatus, ExternalBackupMode, ExternalBackupRun, ExternalDisk
from app.models.app_setting import AppSetting
from app.services.disk_eject import EJECT_NOT_FINISHED_STEP, eject_dedicated_external_disk
from app.services.external_backup_execution import ExternalBackupExecutionService, build_dedicated_datastore_name
from app.services.host_agent import HostAgentClient, HostAgentError, get_pbs_agent_client
from app.services.pbs_api_backend import CREATED_MESSAGE, EJECT_OK_MESSAGE, REUSED_MESSAGE, PBSApiBackend
from app.services.pbs_guards import fit_datastore_name, pbo_marker
from app.services.settings_service import (
    ENV_IMPORT_MARKER_KEY,
    SettingValidationError,
    ensure_pbs_mode,
    import_settings_from_env,
    update_settings,
)
from pbs_fake import FakePBS, fake_settings, make_backend, usb_disk

SERIAL = "WD-123"
TARGET = build_dedicated_datastore_name(SERIAL)  # "pbo-wd-123"


def _disk(db, **overrides) -> ExternalDisk:
    values = dict(
        serial_number=SERIAL,
        display_name="WD Elements",
        capacity_gb=2000,
        connected=True,
        dedicated_backup_disk=True,
        allow_existing_data=False,
        trusted=True,
        reserved_capacity_gb=0,
        source="agent",
        active=True,
        pbs_visible=True,
        pbs_handoff_slot="usb0",
        pbs_device_path="/dev/sdb",
    )
    values.update(overrides)
    disk = ExternalDisk(**values)
    db.add(disk)
    db.commit()
    db.refresh(disk)
    return disk


def _run(db, disk) -> ExternalBackupRun:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    run = ExternalBackupRun(
        disk_id=disk.id,
        status=BackupRunStatus.RUNNING,
        mode=ExternalBackupMode.DEDICATED,
        datastore_name="main",
        target_path="",
        started_at=now,
        created_at=now,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def _prepare(backend, **payload):
    return backend.post(
        "/prepare-dedicated-pbs-datastore",
        {"disk": SERIAL, "datastore_name": TARGET, "confirmation": True, **payload},
    )


def _destructive(fake: FakePBS) -> list[tuple[str, str]]:
    return [call for call in fake.mutating_calls() if call[1] in {"nodes/localhost/disks/wipedisk", "nodes/localhost/disks/directory"}]


# --- full flow -------------------------------------------------------------------------------

def test_full_flow_prepare_sync_eject_and_reuse(db, session_factory):
    disk = _disk(db)
    run = _run(db, disk)
    fake = FakePBS()
    backend = make_backend(fake, session_factory)

    prepared = _prepare(backend, callback_run_id=run.id)
    assert prepared.message == CREATED_MESSAGE
    assert prepared.payload["mount_path"] == f"/mnt/datastore/{TARGET}"
    assert prepared.payload["datastore_name"] == TARGET
    # wipe strictly before creation, both through the API
    assert _destructive(fake) == [
        ("PUT", "nodes/localhost/disks/wipedisk"),
        ("POST", "nodes/localhost/disks/directory"),
    ]
    directory_body = next(body for method, path, body in fake.calls if path == "nodes/localhost/disks/directory")
    assert directory_body == {"disk": "sdb", "name": TARGET, "filesystem": "ext4", "add-datastore": True, "removable-datastore": True}
    config = next(cfg for cfg in fake.datastores if cfg["name"] == TARGET)
    assert config["comment"] == pbo_marker(SERIAL)
    assert config["backing-device"] == f"UUID-{TARGET}"

    exported = backend.post(
        "/run-external-export",
        {"target_path": prepared.payload["mount_path"], "datastore_name": "main", "mode": "dedicated", "target_datastore_name": TARGET, "callback_run_id": run.id},
    )
    assert exported.ok
    assert exported.payload["pbs_sync_job_id"] == f"pbo-sync-{run.id}"
    assert exported.payload["pbs_task_upid"].startswith("UPID:localhost:")
    sync_body = next(body for method, path, body in fake.calls if (method, path) == ("POST", "config/sync"))
    assert sync_body == {
        "id": f"pbo-sync-{run.id}",
        "store": TARGET,
        "remote-store": "main",
        "remove-vanished": False,
        "owner": "root@pam",
        "comment": f"PBO export run {run.id}",
    }
    assert fake.sync_jobs == {}  # temporary job removed after the run
    db.refresh(run)
    assert "sync group vm/101 (2 of 2)" in run.stdout_log  # PBS task log copied into the run log
    # B22: progress lines feed the bar, not the journal.
    assert "percentage done" not in run.stdout_log
    assert run.progress_percent == 100.0

    ejected = backend.post("/eject-dedicated-pbs-datastore", {"serial": SERIAL, "datastore_name": TARGET, "mount_path": "", "callback_run_id": run.id})
    assert ejected.message == EJECT_OK_MESSAGE
    assert fake.mount_status[TARGET] == "notmounted"

    # Next run: same disk plugged again → reused, no formatting, remounted.
    fake.calls.clear()
    again = _prepare(backend)
    assert again.message == REUSED_MESSAGE
    assert _destructive(fake) == []
    assert ("POST", f"admin/datastore/{TARGET}/mount") in fake.mutating_calls()
    assert fake.mount_status[TARGET] == "mounted"


def test_orchestration_uses_api_backend_without_code_changes(db, session_factory):
    """ExternalBackupExecutionService (unchanged) drives the PBS API through get_pbs_agent_client()."""
    disk = _disk(db)
    fake = FakePBS()
    backend = make_backend(fake, session_factory)
    with (
        patch("app.services.external_backup_agent.get_pbs_agent_client", return_value=backend),
        patch("app.services.external_backup_agent.get_host_agent_client", return_value=SimpleNamespace(base_url="host")),
        patch("app.services.external_backup_agent.get_settings", return_value=fake_settings()),
    ):
        result = ExternalBackupExecutionService().execute(disk=disk, datastore_name="main", mode=ExternalBackupMode.DEDICATED)
    assert result.target_datastore_name == TARGET
    assert result.target_path == f"/mnt/datastore/{TARGET}"
    assert result.pbs_sync_job_id is not None and result.pbs_task_upid is not None


def test_sync_failure_reports_log_and_cleans_job(db, session_factory):
    _disk(db)
    fake = FakePBS()
    backend = make_backend(fake, session_factory)
    _prepare(backend)
    fake.sync_exitstatus = "Error: connection lost"
    with pytest.raises(HostAgentError, match="PBS task failed: Error: connection lost") as excinfo:
        backend.post("/run-external-export", {"datastore_name": "main", "mode": "dedicated", "target_datastore_name": TARGET})
    assert "TASK ERROR" in excinfo.value.stdout_log
    assert fake.sync_jobs == {}


def test_coexistence_mode_is_refused_in_api_mode():
    backend = make_backend(FakePBS())
    with pytest.raises(HostAgentError, match="PBS_MODE=agent"):
        backend.post("/run-external-export", {"datastore_name": "main", "mode": "coexistence", "target_datastore_name": TARGET})
    with pytest.raises(HostAgentError) as excinfo:
        backend.post("/prepare-disk", {"disk": SERIAL})
    assert excinfo.value.payload["error_code"] == "unsupported_in_api_mode"


# --- guards (no destructive call may happen) -----------------------------------------------------

@pytest.mark.parametrize(
    ("disk_kwargs", "pbs_disk", "code"),
    [
        ({"trusted": False}, usb_disk(), "disk_not_approved"),
        ({}, usb_disk(size_gib=16), "disk_too_small"),
        ({}, usb_disk(partitions=[{"name": "sdb1", "filesystem": "ext4", "uuid": "U1", "used": "filesystem", "mounted": False}]), "partition_has_data"),
        ({}, usb_disk(partitions=[{"name": "sdb1", "filesystem": "vfat", "uuid": "U1", "used": "filesystem", "mounted": True}]), "partition_mounted"),
        ({}, usb_disk(partitions=[{"name": "sdb1", "uuid": "U1", "used": "lvm"}], used="lvm"), "disk_in_use"),
        ({}, usb_disk(partitions=[{"name": "sdb1", "uuid": "U1", "used": "zfs"}]), "partition_system"),
    ],
)
def test_guards_block_destructive_calls(db, session_factory, disk_kwargs, pbs_disk, code):
    _disk(db, **disk_kwargs)
    fake = FakePBS(disks=[pbs_disk])
    backend = make_backend(fake, session_factory)
    with pytest.raises(HostAgentError) as excinfo:
        _prepare(backend)
    assert excinfo.value.payload["error_code"] == code
    assert _destructive(fake) == []


def test_unknown_disk_and_missing_confirmation(db, session_factory):
    fake = FakePBS()
    backend = make_backend(fake, session_factory)
    with pytest.raises(HostAgentError) as excinfo:
        _prepare(backend)
    assert excinfo.value.payload["error_code"] == "disk_unknown"
    _disk(db)
    with pytest.raises(HostAgentError) as excinfo:
        _prepare(backend, confirmation=False)
    assert excinfo.value.payload["error_code"] == "confirmation_required"
    assert _destructive(fake) == []


def test_existing_datastore_without_pbo_marker_is_never_touched(db, session_factory):
    _disk(db)
    fake = FakePBS(disks=[usb_disk(partitions=[{"name": "sdb1", "filesystem": "ext4", "uuid": "FOREIGN", "used": "filesystem", "mounted": False}])])
    fake.datastores.append({"name": "someone-else", "path": ".", "backing-device": "FOREIGN", "comment": "family photos"})
    fake.mount_status["someone-else"] = "notmounted"
    backend = make_backend(fake, session_factory)
    with pytest.raises(HostAgentError) as excinfo:
        _prepare(backend)
    assert excinfo.value.payload["error_code"] == "datastore_exists"
    assert fake.mutating_calls() == []


def test_datastore_name_taken_and_active_store(db, session_factory):
    _disk(db)
    fake = FakePBS()
    fake.datastores.append({"name": TARGET, "path": "/elsewhere"})
    backend = make_backend(fake, session_factory)
    with pytest.raises(HostAgentError) as excinfo:
        _prepare(backend)
    assert excinfo.value.payload["error_code"] == "datastore_name_taken"

    # Reuse refused while a task runs on the PBO datastore.
    fake2 = FakePBS(disks=[usb_disk(partitions=[{"name": "sdb1", "filesystem": "ext4", "uuid": "MINE", "used": "filesystem", "mounted": True}])])
    fake2.datastores.append({"name": TARGET, "path": ".", "backing-device": "MINE", "comment": pbo_marker(SERIAL)})
    fake2.mount_status[TARGET] = "mounted"
    fake2.add_running_task("verificationjob", f"{TARGET}:v-1")
    with pytest.raises(HostAgentError) as excinfo:
        _prepare(make_backend(fake2, session_factory))
    assert excinfo.value.payload["error_code"] == "datastore_active"


def test_long_serial_datastore_name_fits_pbs_limit():
    name = build_dedicated_datastore_name("WD-WX12345678901234567890123456789012345")
    assert len(name) > 32
    fitted = fit_datastore_name(name)
    assert len(fitted) == 32 and fitted == fit_datastore_name(name) and fitted.startswith("pbo-")


# --- eject ------------------------------------------------------------------------------------------

def _prepared_fake(session_factory, db):
    disk = _disk(db, prepared_as_pbs_datastore=True, pbs_datastore_name=TARGET, pbs_mount_path=f"/mnt/datastore/{TARGET}")
    fake = FakePBS()
    backend = make_backend(fake, session_factory)
    _prepare(backend)
    return disk, fake, backend


def _eject_through_disk_eject(db, disk, backend):
    bridge_factory = lambda: _bridge(backend)  # noqa: E731
    with (
        patch("app.services.disk_eject.get_external_backup_agent_bridge", side_effect=bridge_factory),
        patch("app.services.disk_eject._detach_usb_slot_via_host_agent", return_value=SimpleNamespace(message="ok")) as detach,
        patch("app.services.disk_eject._get_qemu_config_usb_map", return_value={}),
        patch("app.services.disk_eject._attempt_disk_spin_down", return_value=None),
        patch("app.services.disk_eject.notifications.run_finished") as not_finished,
    ):
        try:
            outcome = eject_dedicated_external_disk(db, disk.id)
        except HTTPException as exc:
            outcome = exc
    return outcome, detach, not_finished


def _bridge(backend):
    from app.services.external_backup_agent import ExternalBackupAgentBridge

    with (
        patch("app.services.external_backup_agent.get_pbs_agent_client", return_value=backend),
        patch("app.services.external_backup_agent.get_host_agent_client", return_value=SimpleNamespace(base_url="host")),
        patch("app.services.external_backup_agent.get_settings", return_value=fake_settings()),
    ):
        return ExternalBackupAgentBridge()


def test_eject_unmounts_then_detaches_usb(db, session_factory):
    disk, fake, backend = _prepared_fake(session_factory, db)
    outcome, detach, _ = _eject_through_disk_eject(db, disk, backend)
    assert isinstance(outcome, ExternalDisk)
    assert fake.mount_status[TARGET] == "notmounted"
    detach.assert_called_once()  # USB detach only after the unmount task finished
    run = db.scalars(select(ExternalBackupRun).order_by(ExternalBackupRun.id.desc())).first()
    assert run.status == BackupRunStatus.SUCCESS
    assert "unmounting" in run.stdout_log


def test_eject_timeout_keeps_disk_attached(db, session_factory):
    disk, fake, backend = _prepared_fake(session_factory, db)
    fake.hang_unmount = True
    outcome, detach, not_finished = _eject_through_disk_eject(db, disk, backend)
    assert isinstance(outcome, HTTPException) and outcome.status_code == 504
    detach.assert_not_called()
    not_finished.assert_called_once()
    run = db.scalars(select(ExternalBackupRun).order_by(ExternalBackupRun.id.desc())).first()
    assert run.current_step == EJECT_NOT_FINISHED_STEP
    assert db.get(ExternalDisk, disk.id).connected is True


def test_eject_refused_while_task_runs_and_for_legacy_datastore(db, session_factory):
    disk, fake, backend = _prepared_fake(session_factory, db)
    fake.add_running_task("garbage_collection", TARGET)
    outcome, detach, _ = _eject_through_disk_eject(db, disk, backend)
    assert isinstance(outcome, HTTPException) and outcome.status_code == 409
    detach.assert_not_called()

    legacy = FakePBS()
    legacy.datastores.append({"name": "pbo-legacy", "path": "/mnt/pbo/X/pbs-datastore"})
    with pytest.raises(HostAgentError) as excinfo:
        make_backend(legacy).post("/eject-dedicated-pbs-datastore", {"serial": "X", "datastore_name": "pbo-legacy", "mount_path": ""})
    assert excinfo.value.payload["error_code"] == "legacy_datastore"


# --- read-only endpoints, switch and settings ---------------------------------------------------------

def test_inspect_disk_and_filesystem_usage(db, session_factory):
    _disk(db)
    fake = FakePBS()
    backend = make_backend(fake, session_factory)
    _prepare(backend)
    inspected = backend.post("/inspect-disk", {"disk": "/dev/sdb"})
    assert inspected.payload["disk"]["path"] == "/dev/sdb"
    assert inspected.payload["filesystem_info"]["mount_path"] == f"/mnt/datastore/{TARGET}"
    usage = backend.post("/filesystem-usage", {"mount_path": f"/mnt/datastore/{TARGET}"})
    assert usage.payload["filesystem_total_gb"] == 2000 and usage.payload["filesystem_free_gb"] == 1500
    with pytest.raises(HostAgentError):
        backend.post("/inspect-disk", {"disk": "NOPE"})


def test_export_objects_status_and_cleanup():
    fake = FakePBS()
    fake.sync_jobs["pbo-sync-7"] = {"id": "pbo-sync-7", "store": TARGET, "remote-store": "main"}
    fake.sync_jobs["pbo-sync-71"] = {"id": "pbo-sync-71", "store": TARGET, "remote-store": "main"}
    fake.add_running_task("syncjob", f"-:main:{TARGET}::pbo-sync-71")
    backend = make_backend(fake)
    status = backend.post("/external-export-objects/status", {}).payload
    assert status["active"] is True and status["active_run_id"] == 71  # exact match, not "7"
    assert backend.post("/external-export-objects/cleanup", {}).payload["ok"] is False


def test_get_pbs_agent_client_switches_on_pbs_mode():
    with patch("app.services.host_agent.get_settings", return_value=Settings(pbs_mode="agent")):
        assert isinstance(get_pbs_agent_client(), HostAgentClient)
    with patch("app.services.host_agent.get_settings", return_value=fake_settings()):
        assert isinstance(get_pbs_agent_client(), PBSApiBackend)
    version = make_backend(FakePBS()).get_version()
    assert {"dedicated-pbs-eject", "filesystem-usage"} <= set(version["capabilities"])


def test_ensure_pbs_mode_defaults(db, secret_key):
    assert ensure_pbs_mode(db) == "api"  # fresh install
    assert db.get(AppSetting, "PBS_MODE").value == "api"


def test_ensure_pbs_mode_legacy_env_import_is_agent(db, secret_key):
    import_settings_from_env(db, {"PBS_AGENT_BASE_URL": "http://pbs:8091", "PBS_DATASTORE": "main"})
    assert ensure_pbs_mode(db) == "agent"


def test_ensure_pbs_mode_existing_prepared_disk_is_agent(db, secret_key):
    db.add(AppSetting(key=ENV_IMPORT_MARKER_KEY, value=""))
    db.commit()
    _disk(db, prepared_as_pbs_datastore=True)
    assert ensure_pbs_mode(db) == "agent"


def test_explicit_pbs_mode_is_kept_and_validated(db, secret_key):
    import_settings_from_env(db, {"PBS_MODE": "api", "PBS_AGENT_BASE_URL": "http://pbs:8091"})
    assert ensure_pbs_mode(db) == "api"
    with pytest.raises(SettingValidationError, match="PBS_MODE"):
        update_settings(db, {"PBS_MODE": "magic"})
