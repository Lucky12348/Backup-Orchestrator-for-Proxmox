"""Last external copy of each VM/CT (Resources page, Phase 6b).

A successful external backup run (not a manual eject) copied a VM when its
group filter is empty (the whole datastore) or contains
`group:<vm|ct>/<vmid>`. A VM without any PBS backup was never copied.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ExternalBackupRun, ExternalDisk, VirtualMachine
from app.models.backup_run import BackupRunStatus
from app.models.virtual_machine import VMType
from app.services.run_log import run_kind


def vm_group(vm: VirtualMachine) -> str | None:
    if not vm.external_id:
        return None
    return f"{'ct' if vm.vm_type == VMType.CT else 'vm'}/{vm.external_id}"


def last_external_copies(db: Session, vms: list[VirtualMachine]) -> dict[int, datetime]:
    disks = {disk.id: disk for disk in db.scalars(select(ExternalDisk))}
    runs = [
        run
        for run in db.scalars(
            select(ExternalBackupRun)
            .where(ExternalBackupRun.status == BackupRunStatus.SUCCESS, ExternalBackupRun.finished_at.is_not(None))
            .order_by(ExternalBackupRun.finished_at.desc())
        )
        if run_kind(run, disks.get(run.disk_id)) == "backup"
    ]
    result: dict[int, datetime] = {}
    for vm in vms:
        group = vm_group(vm)
        if group is None or vm.last_backup_at is None:
            continue
        copy = next((run for run in runs if not run.group_filter or f"group:{group}" in run.group_filter), None)
        if copy is not None:
            result[vm.id] = copy.finished_at
    return result
