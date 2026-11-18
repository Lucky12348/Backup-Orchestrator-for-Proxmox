"""Phase 5f.2: a disk pulled out while handed to the PBS VM.

Real test: run 24 without auto-eject, the disk pulled out while passed through
→ no alert, `usb0: host=3-4` left on VM 100, the datastore still mounted on
PBS. Covered: detection (report USB devices, periodic watch, PBS fallback),
the clean-up order (mapping first), a run on that disk, the orphan mappings
at start, the disk plugged back then verified, and the journal of a run where
the verification has nothing to check.
"""

from contextlib import ExitStack
from dataclasses import replace
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from app.models import ActivityEvent, BackupRunStatus, ExternalBackupMode, ExternalBackupRun, ExternalDisk, PbsUsbMapping
from app.schemas.agent import AgentDiskReportCreate
from app.services import passthrough_watch
from app.services.disk_guards import disk_alerts
from app.services.disks import ingest_agent_disk_report
from pbs_fake import FakePBS, fake_settings, make_backend

SERIAL = "PBOTESTA"
STORE = "pbo-pbotesta"
SETTINGS = replace(fake_settings(), pbs_execution_vm_id=100)


class FakeHost:
    """Host agent: USB devices present, VM config, USB detach (calls recorded)."""

    def __init__(self, ports=("3-4",), config="usb0: host=3-4,usb3=1\n"):
        self.ports = list(ports)
        self.config = config
        self.calls: list[tuple[str, dict]] = []

    def get(self, path, params=None):
        self.calls.append(("GET " + path, {}))
        return SimpleNamespace(payload={"devices": [{"port_id": port} for port in self.ports]}, stdout_log=None)

    def post(self, path, payload):
        self.calls.append((path, payload))
        if path == "/qemu/usb/detach":
            self.config = "\n".join(line for line in self.config.splitlines() if not line.startswith(f"{payload['slot']}:"))
        return SimpleNamespace(payload={"config": self.config}, stdout_log=None)


def _handed_off(db, **overrides):
    values = dict(
        serial_number=SERIAL, display_name="PBOTESTA", capacity_gb=500, connected=True, dedicated_backup_disk=True, trusted=True,
        source="agent", active=True, reported_by_hostname="pve-test", handoff_status="attached_to_pbs", pbs_handoff_slot="usb0",
        proxmox_usb_mapping="3-4", usb_location={"port_id": "3-4", "busnum": 3, "devpath": "4"}, pbs_visible=True,
        prepared_as_pbs_datastore=True, pbs_datastore_name=STORE,
    )
    values.update(overrides)
    disk = ExternalDisk(**values)
    db.add(disk)
    db.commit()
    return disk


def _mounted_pbs():
    fake = FakePBS()
    fake.datastores.append({"name": STORE, "path": STORE, "backing-device": "UUID-1", "comment": f"pbo:serial={SERIAL}"})
    fake.mount_status[STORE] = "mounted"
    return fake


def _patches(stack, host, fake, session_factory):
    backend = make_backend(fake, session_factory, settings=SETTINGS)
    stack.enter_context(patch("app.services.passthrough_watch.get_settings", lambda: SETTINGS))
    stack.enter_context(patch("app.services.host_agent.get_host_agent_client", lambda: host))
    stack.enter_context(patch("app.services.host_agent.get_pbs_agent_client", lambda: backend))
    stack.enter_context(patch("app.services.notifications.unsafe_removal", lambda *a: None))
    return backend


def _report(db, *, usb_ports, disks=()):
    payload = AgentDiskReportCreate(
        hostname="pve-test",
        observed_at=datetime.utcnow(),
        disks=[{"serial_number": s, "display_name": s, "capacity_gb": 500, "connected": True, "candidate_type": "usb"} for s in disks],
        usb_devices=[{"port_id": port} for port in usb_ports],
    )
    with patch("app.services.disks.handle_disk_detected", lambda *a: None):
        return ingest_agent_disk_report(db, payload)


def test_pulled_while_passed_through_outside_a_run(db, session_factory):
    disk = _handed_off(db)
    host, fake = FakeHost(), _mounted_pbs()
    with ExitStack() as stack:
        _patches(stack, host, fake, session_factory)
        _report(db, usb_ports=["3-4"])  # still there: the passthrough hides the block device only
        db.refresh(disk)
        assert disk.handoff_status == "attached_to_pbs" and host.calls == []
        _report(db, usb_ports=[])  # USB device gone (udev USB remove → report)
    db.refresh(disk)
    # 1. the usb0 mapping removed first, 2. the datastore unmounted on PBS
    assert host.calls[0] == ("/qemu/usb/detach", {"vmid": 100, "slot": "usb0"})
    assert "usb0" not in host.config
    assert ("POST", f"admin/datastore/{STORE}/unmount") in fake.mutating_calls() and fake.mount_status[STORE] == "notmounted"
    # 3. the disk: pulled out without eject, verify recommended
    assert (disk.status, disk.handoff_status, disk.pbs_handoff_slot, disk.connected) == ("pulled", "pulled", None, False)
    assert disk.verify_recommended is True
    assert db.query(ActivityEvent).filter_by(action="unsafe_removal").count() == 1
    assert [a["code"] for a in disk_alerts(db) if a["disk_id"] == disk.id] == ["verify_recommended"]


def test_pulled_during_a_run_fails_it_cleanly(db, session_factory):
    disk = _handed_off(db)
    now = datetime.utcnow()
    run = ExternalBackupRun(
        disk_id=disk.id, status=BackupRunStatus.RUNNING, started_at=now, created_at=now, target_path="/x", datastore_name="main",
        mode=ExternalBackupMode.DEDICATED, current_step="sync_job",
    )
    db.add(run)
    db.commit()
    host, fake = FakeHost(ports=[]), _mounted_pbs()
    run.pbs_task_upid = fake.add_running_task("syncjob", f"-:main:{STORE}::pbo-sync-{run.id}")
    db.commit()
    with ExitStack() as stack:
        _patches(stack, host, fake, session_factory)
        result = passthrough_watch.reconcile(db, host_client=host)
    assert result["pulled"] == [disk.id]
    assert host.calls[1] == ("/qemu/usb/detach", {"vmid": 100, "slot": "usb0"})  # right after the device list
    assert ("DELETE", f"nodes/localhost/tasks/{run.pbs_task_upid}") in fake.mutating_calls()  # PBS task stopped
    db.refresh(run)
    from app.services.run_log import run_journal

    entries = run_journal(run, disk)["entries"]
    assert any(entry["code"] == "disk_pulled_during_sync" and entry["level"] == "err" for entry in entries)
    # B44: stopped at once, with the stage's message.
    assert (run.status, run.current_step, run.interrupted) == (BackupRunStatus.FAILED, "disk_pulled", "during_sync")
    assert run.message.startswith("Interrupted: the disk was pulled out during the copy")
    assert db.get(ExternalDisk, disk.id).handoff_status == "pulled"


def test_pbs_is_asked_when_the_host_cannot_tell(db, session_factory):
    """Old host agent (no USB device list): two PBS checks in a row."""
    disk = _handed_off(db)

    class OldHost(FakeHost):
        def get(self, path, params=None):
            raise RuntimeError("404 Not Found")

    host, fake = OldHost(), _mounted_pbs()
    fake.disks = []  # PBS no longer sees the disk
    passthrough_watch._pbs_misses.clear()
    with ExitStack() as stack:
        backend = _patches(stack, host, fake, session_factory)
        assert passthrough_watch.reconcile(db, host_client=host, pbs_client=backend)["pulled"] == []  # once: maybe a glitch
        assert passthrough_watch.reconcile(db, host_client=host, pbs_client=backend)["pulled"] == [disk.id]


def test_orphan_mappings_are_removed_at_start(db, session_factory):
    now = datetime.utcnow()
    gone = ExternalDisk(serial_number="OLD", display_name="old", capacity_gb=500, connected=False, source="agent", active=False)
    in_use = _handed_off(db, serial_number="KEEP", pbs_handoff_slot="usb1", proxmox_usb_mapping="3-5", usb_location={"port_id": "3-5"})
    db.add(gone)
    db.commit()
    db.add_all(
        [
            PbsUsbMapping(vmid=100, slot="usb0", host_value="3-4", disk_id=gone.id, disk_serial="OLD", created_at=now),
            PbsUsbMapping(vmid=100, slot="usb1", host_value="3-5", disk_id=in_use.id, disk_serial="KEEP", created_at=now),
            PbsUsbMapping(vmid=100, slot="usb2", host_value="1-1", disk_id=None, created_at=now),
        ]
    )
    db.commit()
    host = FakeHost(ports=["3-5"], config="usb0: host=3-4,usb3=1\nusb1: host=3-5\nusb2: host=9-9\n")
    with ExitStack() as stack:
        _patches(stack, host, _mounted_pbs(), session_factory)
        result = passthrough_watch.reconcile(db, host_client=host, cleanup_orphans=True)
    assert result["orphans"] == ["usb0=3-4"]
    assert ("/qemu/usb/detach", {"vmid": 100, "slot": "usb0"}) in host.calls
    assert ("/qemu/usb/detach", {"vmid": 100, "slot": "usb2"}) not in host.calls  # not PBO's value any more: untouched
    reasons = {row.slot: row.release_reason for row in db.query(PbsUsbMapping)}
    assert reasons == {"usb0": "orphan", "usb1": None, "usb2": "gone"}


def test_plugged_back_then_verified(db, session_factory):
    disk = _handed_off(db)
    host, fake = FakeHost(ports=[]), _mounted_pbs()
    with ExitStack() as stack:
        _patches(stack, host, fake, session_factory)
        passthrough_watch.reconcile(db, host_client=host)
        _report(db, usb_ports=["3-4"], disks=[SERIAL])  # plugged back on the host
    db.refresh(disk)
    assert (disk.status, disk.handoff_status, disk.connected, disk.verify_recommended) == ("known", None, True, True)
    from app.services.backup_readiness import local_disk_reasons
    from app.services.external_backups import _verify_only_reasons

    assert local_disk_reasons(db, disk) == []
    with patch("app.services.external_backups.get_settings", lambda: SETTINGS):
        assert _verify_only_reasons(disk) == []  # the one-click verification can start
    # A failed verification: the alert tells what to do instead.
    now = datetime.utcnow()
    db.add(
        ExternalBackupRun(
            disk_id=disk.id, status=BackupRunStatus.FAILED, started_at=now, finished_at=now, created_at=now, target_path="/x",
            datastore_name="main", mode=ExternalBackupMode.DEDICATED, verify_only=True, current_step="verify_failed",
        )
    )
    db.commit()
    assert [(a["code"], a["level"]) for a in disk_alerts(db) if a["disk_id"] == disk.id] == [("verify_failed", "danger")]


def test_offline_datastore_unmounts_stacked_mounts_without_failing():
    fake = _mounted_pbs()
    remaining = {"mounts": 2}  # sdc1 then sdd1 stacked on the same path

    original = fake._route

    def route(method, path, body, params):
        if method == "POST" and path == f"admin/datastore/{STORE}/unmount":
            def effect():
                remaining["mounts"] -= 1
                fake.mount_status[STORE] = "mounted" if remaining["mounts"] else "notmounted"

            return fake._task("unmount-device", STORE, ["unmounting"], effect)
        return original(method, path, body, params)

    fake._route = route
    result = make_backend(fake).post("/offline-removable-datastore", {"datastore_name": STORE})
    assert result.payload["state"] == "notmounted" and remaining["mounts"] == 0
