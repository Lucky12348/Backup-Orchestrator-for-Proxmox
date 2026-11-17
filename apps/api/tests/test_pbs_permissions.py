"""Phase 5f.1: privileges of the PBO token on PBS.

Real test: re-creating a deleted datastore configuration (POST
/config/datastore) failed with 403 — it needs Datastore.Allocate, which the
DatastoreAdmin role does not hold (the first creation went through
disks/directory, Sys.Modify only). Covered here: the ACLs the wizard sets,
the readiness check of the token's effective privileges, the readable message
when a 403 still happens during a run, and the Resources copies per disk.
"""

from dataclasses import replace
from datetime import datetime
from unittest.mock import patch

import httpx
import pytest
from fastapi import HTTPException

from app.models import BackupRunStatus, ExternalBackupMode, ExternalBackupRun, ExternalDisk, VirtualMachine
from app.models.virtual_machine import VMType
from app.services import pbs_permissions
from app.services.disk_contents import copies_by_group
from app.services.external_backup_execution import build_dedicated_datastore_name
from app.services.external_backups import run_external_backup
from app.services.host_agent import HostAgentError
from app.services.pbs_client import PBSClient
from app.services.pbs_provisioning import PBS_ACLS
from app.services.settings_service import update_settings
from pbs_fake import FakePBS, fake_settings, make_backend, usb_disk

SERIAL = "WD-123"
TARGET = build_dedicated_datastore_name(SERIAL)
TOKEN = "pbo@pbs!pbo"
NOW = datetime(2026, 11, 16, 12, 0)


def _settings():
    return replace(fake_settings(), pbs_token_id=TOKEN)


def _client(fake):
    return PBSClient(_settings(), transport=httpx.MockTransport(fake.handler))


def _without_allocate(fake):
    fake.privileges["/datastore"] = [p for p in fake.privileges["/datastore"] if p != "Datastore.Allocate"]


def test_wizard_grants_admin_on_datastore_for_datastore_allocate():
    assert dict(PBS_ACLS) == {"/system/disks": "Admin", "/system/tasks": "Audit", "/datastore": "Admin"}


def test_missing_allocate_is_a_clear_reason_with_the_exact_command():
    fake = FakePBS()
    _without_allocate(fake)
    found = pbs_permissions.reason(pbs_permissions.missing_privileges(_client(fake)), TOKEN)
    assert found["code"] == "pbs_token_cannot_create_datastore"
    assert found["privileges"] == "Datastore.Allocate (/datastore)"
    assert found["command"] == (
        "proxmox-backup-manager acl update /datastore Admin --auth-id pbo@pbs && "
        "proxmox-backup-manager acl update /datastore Admin --auth-id 'pbo@pbs!pbo'"
    )
    assert found["message"].startswith("The PBO token cannot create a datastore on PBS")


def test_other_missing_privileges_and_unknown_answers():
    fake = FakePBS()
    fake.privileges["/system/tasks"] = []
    found = pbs_permissions.reason(pbs_permissions.missing_privileges(_client(fake)), TOKEN)
    assert found["code"] == "pbs_token_missing_privileges" and "acl update /system/tasks Audit" in found["command"]
    assert pbs_permissions.missing_privileges(_client(FakePBS())) == {}

    class Broken:
        def permissions(self, path):
            raise RuntimeError("PBS down")

    assert pbs_permissions.missing_privileges(Broken()) is None  # no verdict, no refusal


def test_no_run_without_the_privileges(db):
    pbs_permissions.reset_cache()
    update_settings(db, {"PBS_MODE": "api", "PBS_API_URL": "https://pbs.test:8007/api2/json", "PBS_TOKEN_ID": TOKEN, "PBS_DATASTORE": "main"})
    disk = ExternalDisk(serial_number=SERIAL, display_name="WD", capacity_gb=2000, connected=True, dedicated_backup_disk=True, trusted=True, source="agent", active=True)
    db.add(disk)
    db.commit()
    fake = FakePBS()
    _without_allocate(fake)
    with (
        patch("app.services.pbs_client.PBSClient", lambda settings: _client(fake)),
        patch("app.services.external_backups.assert_no_active_external_backup", lambda db: None),
    ):
        with pytest.raises(HTTPException) as refused:
            run_external_backup(db, disk.id, True)
    assert refused.value.status_code == 409 and refused.value.detail["code"] == "pbs_token_cannot_create_datastore"
    assert db.query(ExternalBackupRun).count() == 0


def test_a_403_during_the_run_is_readable_and_never_wipes(db, session_factory):
    """The recreate path of the real test: UUID proven, POST /config/datastore refused."""
    disk = ExternalDisk(
        serial_number=SERIAL, display_name="WD", capacity_gb=2000, connected=True, dedicated_backup_disk=True, trusted=True,
        source="agent", active=True, pbs_visible=True, pbs_handoff_slot="usb0", pbs_device_path="/dev/sdb",
    )
    db.add(disk)
    db.commit()
    fake = FakePBS(disks=[usb_disk(SERIAL, partitions=[])])
    backend = make_backend(fake, session_factory, settings=_settings())
    assert backend.post("/prepare-dedicated-pbs-datastore", {"disk": SERIAL, "datastore_name": TARGET, "confirmation": True}).ok
    db.refresh(disk)
    disk.prepared_as_pbs_datastore, disk.pbs_datastore_name = True, TARGET
    db.commit()
    fake.datastores = [cfg for cfg in fake.datastores if cfg["name"] != TARGET]  # `datastore remove`
    fake.mount_status.pop(TARGET, None)
    _without_allocate(fake)
    fake.calls.clear()
    with pytest.raises(HostAgentError) as refused:
        backend.post("/prepare-dedicated-pbs-datastore", {"disk": SERIAL, "datastore_name": TARGET, "confirmation": True})
    assert refused.value.payload["error_code"] == "pbs_token_cannot_create_datastore"
    assert "HTTP 403" not in str(refused.value) and "acl update /datastore Admin --auth-id pbo@pbs" in str(refused.value)
    assert not [c for c in fake.mutating_calls() if c[1] in {"nodes/localhost/disks/wipedisk", "nodes/localhost/disks/directory"}]
    from app.services.run_log import _pbo_entry

    assert _pbo_entry(str(refused.value), at=None, step="failure").code == "pbs_token_cannot_create_datastore"


def test_resources_copies_from_runs_when_the_contents_were_never_read(db):
    """PBOTESTA unplugged / its config deleted, no cached contents: its
    successful backups still say what it holds, and when."""
    db.add_all(
        [
            VirtualMachine(name="web", vm_type=VMType.VM, external_id="100", source="proxmox", last_backup_at=NOW),
            VirtualMachine(name="logs", vm_type=VMType.CT, external_id="101", source="proxmox", last_backup_at=NOW),
            VirtualMachine(name="never", vm_type=VMType.VM, external_id="102", source="proxmox"),
        ]
    )
    disk = ExternalDisk(serial_number="PBOTESTA", display_name="PBOTESTA", capacity_gb=500, connected=False, source="agent", active=False)
    db.add(disk)
    db.commit()
    for day, status, groups in ((14, BackupRunStatus.SUCCESS, None), (15, BackupRunStatus.SUCCESS, ["group:vm/100"]), (16, BackupRunStatus.FAILED, None)):
        at = datetime(2026, 11, day, 18, 0)
        db.add(
            ExternalBackupRun(
                disk_id=disk.id, status=status, started_at=at, finished_at=at, created_at=at, target_path="/x", datastore_name="main",
                mode=ExternalBackupMode.DEDICATED, group_filter=groups,
            )
        )
    db.commit()
    copies = copies_by_group(db)
    assert [(c["disk_name"], c["newest"], c["source"]) for c in copies["vm/100"]] == [("PBOTESTA", "2026-11-15T18:00:00Z", "runs")]
    assert copies["ct/101"][0]["newest"] == "2026-11-14T18:00:00Z"
    assert "vm/102" not in copies  # no PBS backup, nothing copied
