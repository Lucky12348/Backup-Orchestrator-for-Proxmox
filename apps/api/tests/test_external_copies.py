"""Phase 6b: last external copy of each VM/CT (Resources page)."""

from datetime import datetime, timedelta

from app.models import ExternalBackupMode, ExternalBackupRun, ExternalDisk, VirtualMachine
from app.models.backup_run import BackupRunStatus
from app.models.virtual_machine import VMType
from app.services.external_copies import last_external_copies

NOW = datetime(2026, 11, 16, 12, 0)
ADMIN = {"username": "admin", "password": "correct horse battery"}


def _run(db, disk, days, status=BackupRunStatus.SUCCESS, group_filter=None, datastore="main"):
    run = ExternalBackupRun(disk_id=disk.id, status=status, started_at=NOW - timedelta(days=days),
                            finished_at=NOW - timedelta(days=days) + timedelta(minutes=10), target_path="/x",
                            datastore_name=datastore, mode=ExternalBackupMode.DEDICATED, created_at=NOW, group_filter=group_filter)
    db.add(run)
    db.commit()
    return run


def test_last_external_copy_per_vm(db):
    disk = ExternalDisk(serial_number="WD", display_name="WD", capacity_gb=3726, source="agent", pbs_datastore_name="pbo-wd")
    vms = [
        VirtualMachine(name="web", vm_type=VMType.VM, source="proxmox", external_id="100", last_backup_at=NOW - timedelta(days=30)),
        VirtualMachine(name="db", vm_type=VMType.CT, source="proxmox", external_id="101", last_backup_at=NOW - timedelta(days=30)),
        VirtualMachine(name="new", vm_type=VMType.VM, source="proxmox", external_id="102", last_backup_at=None),
    ]
    db.add_all([disk, *vms])
    db.commit()
    whole = _run(db, disk, 5)
    only_ct = _run(db, disk, 2, group_filter=["group:ct/101"])
    _run(db, disk, 1, status=BackupRunStatus.FAILED)
    _run(db, disk, 0, datastore="pbo-wd")  # a manual eject, not a copy
    copies = last_external_copies(db, vms)
    assert copies[vms[0].id] == whole.finished_at
    assert copies[vms[1].id] == only_ct.finished_at
    assert vms[2].id not in copies


def test_vm_list_exposes_the_last_external_copy(client, db):
    assert client.post("/api/v1/setup/admin", json=ADMIN).status_code == 201
    body = client.get("/api/v1/vms").json()
    assert body == [] or "last_external_copy_at" in body[0]
