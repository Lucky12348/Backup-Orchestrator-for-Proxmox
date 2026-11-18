"""Phase 5h.1: eject any plugged disk; "Run now" on a Proxmox backup job."""

from unittest.mock import patch

import pytest
from fastapi import HTTPException

from app.models import BackupRunStatus, ExternalBackupRun, ExternalDisk
from app.services import pve_job_runs
from app.services.disk_eject import eject_dedicated_external_disk
from app.services.setup_wizard import PVE_PRIVILEGES

UPID = "UPID:pve:0001A2B3:00C4D5E6:673A1B2C:vzdump::root@pam:"


# --- eject from the host -------------------------------------------------------------------------


def _disk(db, **extra):
    values = {"serial_number": "ROT1", "display_name": "Rotation A", "capacity_gb": 2000, "source": "agent", "connected": True, "trusted": True, "active": True}
    values.update(extra)
    disk = ExternalDisk(**values)
    db.add(disk)
    db.commit()
    return disk


def test_a_disk_plugged_into_the_host_only_is_spun_down_and_ejected(db):
    disk = _disk(db)
    with (
        patch("app.services.disk_eject._attempt_disk_spin_down", return_value="Disk spin-down/power-off attempted.") as spin,
        patch("app.services.disk_eject.get_external_backup_agent_bridge") as bridge,
    ):
        disk = eject_dedicated_external_disk(db, disk.id)
    spin.assert_called_once()
    bridge.assert_not_called()  # nothing to unmount on PBS
    assert (disk.status, disk.connected) == ("ejected", False)
    run = db.query(ExternalBackupRun).one()
    assert (run.status, run.current_step) == (BackupRunStatus.SUCCESS, "ejected")
    assert "Disk not handed to PBS" in run.stdout_log


@pytest.mark.parametrize(("extra", "code"), [({"handoff_status": "ejected", "connected": False}, "disk_already_ejected"), ({"connected": False}, "disk_not_connected")])
def test_eject_refusals(db, extra, code):
    disk = _disk(db, **extra)
    with pytest.raises(HTTPException) as error:
        eject_dedicated_external_disk(db, disk.id)
    assert error.value.status_code == 409 and error.value.detail["code"] == code


# --- Run now -----------------------------------------------------------------------------------------


class FakePve:
    def __init__(self, job=None, error=None, status=None):
        self.job = job if job is not None else {
            "id": "backup-abc", "schedule": "sun 03:00", "enabled": 1, "type": "vzdump", "comment": "weekly", "next-run": 1,
            "storage": "pbs", "vmid": "100,101", "mode": "snapshot", "prune-backups": {"keep-last": "3", "keep-weekly": "2"}, "node": "pve2",
        }
        self.error = error
        self.status = status or {"status": "stopped", "exitstatus": "OK"}
        self.posts = []

    def get_backup_job(self, job_id):
        return self.job

    def _post(self, path, data=None):
        self.posts.append((path, data))
        if self.error:
            raise RuntimeError(self.error)
        return UPID.replace("pve", "pve2", 1)

    def _get(self, path):
        return self.status


def test_run_now_posts_the_job_parameters_to_vzdump_on_its_node():
    fake = FakePve()
    started = pve_job_runs.run_job_now(fake, "backup-abc", "pve")
    (path, data), = fake.posts
    assert path == "nodes/pve2/vzdump" and started["node"] == "pve2"
    assert data == {"storage": "pbs", "vmid": "100,101", "mode": "snapshot", "prune-backups": "keep-last=3,keep-weekly=2"}


def test_run_now_without_selection_or_node():
    with pytest.raises(pve_job_runs.JobRunError) as error:
        pve_job_runs.run_job_now(FakePve(job={"id": "x", "storage": "pbs", "all": 0}), "x", "pve")
    assert error.value.code == "job_nothing_selected"
    assert pve_job_runs.run_job_now(FakePve(job={"id": "x", "storage": "pbs", "all": 1}), "x", "pve")["node"] == "pve"


def test_a_token_without_the_privilege_gets_the_exact_command():
    fake = FakePve(error="HTTP 403 for POST /api2/json/nodes/pve2/vzdump: Permission check failed (/vms/100, VM.Backup)")
    with pytest.raises(pve_job_runs.JobRunError) as error:
        pve_job_runs.run_job_now(fake, "backup-abc", "pve")
    assert error.value.code == "pve_permission_missing"
    assert error.value.extra["command"].startswith("pveum role modify PBO --privs")
    assert "VM.Backup" in error.value.extra["command"] and "Datastore.AllocateSpace" in error.value.extra["command"]


def test_task_status_follows_the_upid():
    upid = UPID.replace("pve", "pve2", 1)
    assert pve_job_runs.task_status(FakePve(), "pve2", upid) == {"node": "pve2", "upid": upid, "running": False, "ok": True, "exitstatus": "OK"}
    running = pve_job_runs.task_status(FakePve(status={"status": "running"}), "pve2", upid)
    assert (running["running"], running["ok"]) == (True, None)
    with pytest.raises(pve_job_runs.JobRunError):
        pve_job_runs.task_status(FakePve(), "other", upid)  # the node must be the UPID's


def test_the_minimal_privileges_are_requested():
    assert {"VM.Backup", "Datastore.AllocateSpace"} <= set(PVE_PRIVILEGES)
