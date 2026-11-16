"""Phase 5f: disk sheet and safety guards, against a simulated PBS.

Retention (prune + GC after the sync), verification of the copied snapshots,
space estimate, SMART block/override, removal without eject, USB 2.0,
forgotten rotation, usage alerts, contents cache, photos, disk kind.
"""

from __future__ import annotations

import io
from contextlib import ExitStack
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
from fastapi import HTTPException

from app.models import ActivityEvent, BackupRunStatus, ExternalBackupMode, ExternalBackupRun, ExternalDisk
from app.services import capacity, disk_contents, disk_guards, disk_profile
from app.services.external_backup_execution import build_dedicated_datastore_name
from app.services.external_backups import execute_external_backup_run, run_external_backup
from app.services.pbs_client import PBSClient
from app.services.run_log import run_journal
from app.services.settings_service import update_settings
from pbs_fake import GIB, FakePBS, fake_settings, make_backend, usb_disk

SERIAL = "WD-123"
TARGET = build_dedicated_datastore_name(SERIAL)
T0 = 1_700_000_000
DAY = 86_400


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


def _run(db, disk, **overrides) -> ExternalBackupRun:
    now = datetime.utcnow()
    values = dict(
        disk_id=disk.id, status=BackupRunStatus.PENDING, started_at=now, created_at=now, target_path="/x",
        datastore_name="main", mode=ExternalBackupMode.DEDICATED, current_step="starting",
    )
    values.update(overrides)
    run = ExternalBackupRun(**values)
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def _flow(stack: ExitStack, fake: FakePBS, session_factory):
    """Patches for a whole run in PBS_MODE=api (hand-off simulated)."""
    backend = make_backend(fake, session_factory)
    settings = fake_settings()
    for target, value in (
        ("app.services.external_backups.SessionLocal", session_factory),
        ("app.services.external_backups.handoff_disk_to_pbs", lambda *a, **k: SimpleNamespace(message="handoff ok")),
        ("app.services.external_backup_agent.get_pbs_agent_client", lambda: backend),
        ("app.services.external_backup_agent.get_host_agent_client", lambda: SimpleNamespace(base_url="host")),
        ("app.services.external_backup_agent.get_settings", lambda: settings),
        ("app.services.post_sync.get_settings", lambda: settings),
        ("app.services.external_backups.get_settings", lambda: settings),
        ("app.services.disks.get_pbs_agent_client", lambda: backend),
        ("app.services.external_backups.notify_backup_success", lambda *a, **k: None),
        ("app.services.external_backups.notify_backup_failure", lambda *a, **k: None),
        ("app.services.disk_profile.record_usage", lambda *a, **k: None),
    ):
        stack.enter_context(patch(target, value))
    return backend


def _source(fake: FakePBS) -> None:
    for day in range(3):
        fake.add_snapshot("main", "vm/100", T0 + day * DAY, size=20 * GIB)
    fake.add_snapshot("main", "ct/101", T0, size=4 * GIB)


def _codes(db, run_id):
    run = db.get(ExternalBackupRun, run_id)
    db.refresh(run)
    return [entry["code"] for entry in run_journal(run, None)["entries"] if entry["code"]]


# --- B4 retention -------------------------------------------------------------------------------
def test_retention_prunes_then_garbage_collects_after_the_sync(db, session_factory):
    fake = FakePBS()
    _source(fake)
    disk = _disk(db, retention={"keep_last": 2, "keep_weekly": None, "keep_monthly": None})
    run = _run(db, disk)
    with ExitStack() as stack:
        _flow(stack, fake, session_factory)
        execute_external_backup_run(run.id)
    db.expire_all()
    assert db.get(ExternalBackupRun, run.id).status == BackupRunStatus.SUCCESS
    assert fake.prune_calls == [{"store": TARGET, "keep-last": 2}]
    assert fake.gc_calls == [TARGET]
    # Order: sync → prune → gc.
    mutating = [path for _, path in fake.mutating_calls()]
    assert mutating.index(f"admin/datastore/{TARGET}/prune-datastore") > mutating.index(next(p for p in mutating if p.startswith("admin/sync/")))
    assert mutating.index(f"admin/datastore/{TARGET}/gc") > mutating.index(f"admin/datastore/{TARGET}/prune-datastore")
    codes = _codes(db, run.id)
    assert {"cleanup_retention", "cleanup_gc", "cleanup_done", "contents_updated"} <= set(codes)
    # Contents cached: vm/100 kept 2 versions, ct/101 one.
    disk = db.get(ExternalDisk, disk.id)
    items = {item["group"]: item for item in disk.contents_cache["items"]}
    assert (items["vm/100"]["versions"], items["ct/101"]["versions"]) == (2, 1)
    assert disk.contents_cached_at is not None


def test_default_retention_keeps_the_last_four_versions():
    disk = ExternalDisk(retention=None)
    assert disk_profile.prune_options(disk_profile.retention_of(disk)) == {"keep-last": 4}
    with pytest.raises(disk_profile.RetentionError):
        disk_profile.normalize_retention({"keep_last": None, "keep_weekly": None, "keep_monthly": None})
    assert disk_profile.normalize_retention({"keep_last": 3, "keep_weekly": 4, "keep_monthly": "6"}) == {"keep_last": 3, "keep_weekly": 4, "keep_monthly": 6}


def test_prune_without_keep_option_is_refused():
    backend = make_backend(FakePBS())
    backend.client.list_admin_datastores = lambda: [{"store": TARGET, "mount-status": "mounted"}]
    from app.services.host_agent import HostAgentError

    with pytest.raises(HostAgentError) as excinfo:
        backend.post("/prune-datastore", {"datastore_name": TARGET, "keep": {}})
    assert excinfo.value.payload["error_code"] == "retention_missing"


# --- B10 verification ------------------------------------------------------------------------------
def test_verify_after_backup_checks_only_the_copied_snapshots(db, session_factory):
    fake = FakePBS()
    _source(fake)
    # The disk already holds vm/100 day 0 (copied by an earlier run).
    fake.snapshots[TARGET] = [{"backup-type": "vm", "backup-id": "100", "backup-time": T0, "size": 20 * GIB}]
    disk = _disk(db)
    run = _run(db, disk, verify_after_backup=True)
    with ExitStack() as stack:
        _flow(stack, fake, session_factory)
        execute_external_backup_run(run.id)
    db.expire_all()
    assert db.get(ExternalBackupRun, run.id).status == BackupRunStatus.SUCCESS
    verified = sorted((call["backup-type"], call["backup-id"], call["backup-time"]) for call in fake.verify_calls)
    assert verified == [("ct", "101", T0), ("vm", "100", T0 + DAY), ("vm", "100", T0 + 2 * DAY)]
    assert all(call["ignore-verified"] is False for call in fake.verify_calls)
    assert {"verify_copied", "verify_ok"} <= set(_codes(db, run.id))


def test_failed_verification_fails_the_run(db, session_factory):
    fake = FakePBS()
    _source(fake)
    fake.verify_exitstatus = "verification failed"
    disk = _disk(db)
    run = _run(db, disk, verify_after_backup=True)
    with ExitStack() as stack:
        _flow(stack, fake, session_factory)
        execute_external_backup_run(run.id)
    db.expire_all()
    stored = db.get(ExternalBackupRun, run.id)
    assert (stored.status, stored.current_step) == (BackupRunStatus.FAILED, "verify_failed")
    assert "verify_failed" in _codes(db, run.id)


def test_without_the_option_nothing_is_verified(db, session_factory):
    fake = FakePBS()
    _source(fake)
    run = _run(db, _disk(db))
    with ExitStack() as stack:
        _flow(stack, fake, session_factory)
        execute_external_backup_run(run.id)
    assert fake.verify_calls == []


def test_verify_only_run_never_formats_and_clears_the_recommendation(db, session_factory):
    fake = FakePBS(disks=[usb_disk()])  # NTFS disk, no PBO datastore
    disk = _disk(db, prepared_as_pbs_datastore=True, pbs_datastore_name=TARGET, verify_recommended=True)
    run = _run(db, disk, verify_only=True, verify_after_backup=True)
    with ExitStack() as stack:
        _flow(stack, fake, session_factory)
        execute_external_backup_run(run.id)
    db.expire_all()
    stored = db.get(ExternalBackupRun, run.id)
    assert stored.status == BackupRunStatus.FAILED
    assert not any("wipedisk" in path or "disks/directory" in path for _, path in fake.mutating_calls())
    assert db.get(ExternalDisk, disk.id).verify_recommended is True


def test_verify_only_run_verifies_everything(db, session_factory):
    fake = FakePBS()
    _source(fake)
    disk = _disk(db, verify_recommended=True, unsafe_removal_at=datetime.utcnow())
    # First a backup (creates the PBO datastore), then the verify-only run.
    with ExitStack() as stack:
        _flow(stack, fake, session_factory)
        execute_external_backup_run(_run(db, disk).id)
        fake.verify_calls.clear()
        db.expire_all()
        run = _run(db, db.get(ExternalDisk, disk.id), verify_only=True, verify_after_backup=True)
        execute_external_backup_run(run.id)
    db.expire_all()
    assert db.get(ExternalBackupRun, run.id).status == BackupRunStatus.SUCCESS
    assert fake.verify_calls == [{"store": TARGET, "ignore-verified": False}]
    assert db.get(ExternalDisk, disk.id).verify_recommended is None
    assert "verify_only_start" in _codes(db, run.id)


# --- B5 capacity ---------------------------------------------------------------------------------
def test_estimate_counts_new_groups_fully_and_increments_partially():
    source = [
        {"backup-type": "vm", "backup-id": "100", "backup-time": T0, "size": 100 * GIB},
        {"backup-type": "vm", "backup-id": "100", "backup-time": T0 + DAY, "size": 100 * GIB},
        {"backup-type": "ct", "backup-id": "101", "backup-time": T0, "size": 10 * GIB},
    ]
    estimate = capacity.estimate_bytes(source, {f"vm/100/{T0}"})
    # vm/100 is on the disk: 10 % of the new snapshot; ct/101 is new: full size.
    assert estimate == {"needed_bytes": 20 * GIB, "new_snapshots": 2, "new_groups": 1}
    assert capacity.estimate_bytes(source, set(), ["group:ct/101"])["needed_bytes"] == 10 * GIB


def _api_settings(db):
    update_settings(db, {"PBS_MODE": "api", "PBS_API_URL": "https://pbs.test:8007/api2/json", "PBS_TOKEN_ID": "root@pam!pbo", "PBS_DATASTORE": "main"})


def test_backup_refused_with_numbers_when_space_is_insufficient(db):
    capacity.reset_cache()
    _api_settings(db)
    fake = FakePBS()
    fake.add_snapshot("main", "vm/100", T0, size=300 * GIB)
    disk = _disk(db, prepared_as_pbs_datastore=True, pbs_datastore_name=TARGET, filesystem_free_gb=120, filesystem_total_gb=1800, filesystem_used_gb=1680)
    disk.contents_cache = {"items": [], "snapshots": []}
    db.commit()
    client = PBSClient(fake_settings(), transport=httpx.MockTransport(fake.handler))
    estimate = capacity.check_space(db, disk, client=client)
    assert (estimate["sufficient"], estimate["needed_bytes"], estimate["free_bytes"]) == (False, 300 * GIB, 120 * GIB)
    reason = capacity.space_reason(estimate)
    assert (reason["code"], reason["needed_gib"], reason["free_gib"]) == ("insufficient_space", 300.0, 120.0)

    with (
        patch("app.services.pbs_client.PBSClient", lambda settings: client),
        patch("app.services.external_backups.assert_no_active_external_backup", lambda db: None),
    ):
        with pytest.raises(HTTPException) as excinfo:
            run_external_backup(db, disk.id, True)
    assert excinfo.value.status_code == 409 and excinfo.value.detail["code"] == "insufficient_space"
    assert db.query(ExternalBackupRun).count() == 0


def test_unknown_contents_never_refuses(db):
    capacity.reset_cache()
    _api_settings(db)
    fake = FakePBS()
    fake.add_snapshot("main", "vm/100", T0, size=300 * GIB)
    disk = _disk(db, prepared_as_pbs_datastore=True, pbs_datastore_name=TARGET, filesystem_free_gb=10)
    estimate = capacity.check_space(db, disk, client=PBSClient(fake_settings(), transport=httpx.MockTransport(fake.handler)))
    assert estimate["known"] is False and capacity.space_reason(estimate) is None


def test_usage_alerts_at_80_and_90_percent_notify_once(db):
    disk = _disk(db, prepared_as_pbs_datastore=True, filesystem_total_gb=1000, filesystem_used_gb=700)
    sent = []
    with patch("app.services.notifications.notify_disk_space", lambda *args: sent.append(args)):
        now = datetime(2026, 11, 1)
        assert disk_profile.record_usage(disk, now=now) is None
        disk.filesystem_used_gb = 820
        assert disk_profile.record_usage(disk, now=now + timedelta(days=1)) == 80
        assert disk_profile.record_usage(disk, now=now + timedelta(days=1, hours=1)) is None  # same level: once
        disk.filesystem_used_gb = 950
        assert disk_profile.record_usage(disk, now=now + timedelta(days=2)) == 90
    assert [args[1] for args in sent] == [80, 90]
    # One sample per day (the latest of the day wins).
    assert [sample["used_gb"] for sample in disk.usage_history] == [700, 820, 950]
    db.commit()
    alerts = disk_guards.disk_alerts(db)
    assert [(a["code"], a["level"], a["params"]["percent"]) for a in alerts] == [("usage_critical", "danger", 95.0)]


# --- B6 SMART -----------------------------------------------------------------------------------
def test_smart_danger_blocks_unless_explicitly_confirmed(db):
    from app.services.backup_readiness import local_disk_reasons

    disk = _disk(db, smart_status="danger", smart_details={"reasons": ["smart_failed"]})
    reasons = local_disk_reasons(db, disk)
    assert reasons[0]["code"] == "smart_failed" and reasons[0]["overridable"] is True
    assert local_disk_reasons(db, disk, smart_override=True) == []
    disk.smart_status = "warning"
    assert local_disk_reasons(db, disk) == []  # "to watch" never blocks
    assert [a["code"] for a in disk_guards.disk_alerts(db)] == ["smart_warning"]


def test_agent_report_stores_smart_speed_and_kind(db):
    from app.schemas.agent import AgentDiskReportCreate
    from app.services.disks import ingest_agent_disk_report

    payload = AgentDiskReportCreate(
        hostname="pve",
        observed_at=datetime.utcnow(),
        disks=[
            {
                "serial_number": "TOSH-1", "display_name": "Canvio", "model_name": "TOSHIBA Canvio Basics", "capacity_gb": 1863,
                "connected": True, "candidate_type": "usb", "rotational": True, "transport": "usb",
                "usb_location": {"busnum": 2, "devpath": "3", "port_id": "2-3", "speed_mbps": 480},
                "smart": {"status": "danger", "reasons": ["smart_failed"], "reallocated": 40},
            }
        ],
    )
    sent = []
    with patch("app.services.disks.notify_disk_health", lambda *args: sent.append(args)), patch("app.services.disks.handle_disk_detected", lambda *a: None):
        (disk,) = ingest_agent_disk_report(db, payload)
    assert (disk.smart_status, disk.smart_details["reallocated"], disk.usb_speed_mbps, disk.kind) == ("danger", 40, 480, "hdd_portable")
    assert sent == [("Canvio", "danger", "smart_failed")]
    assert {a["code"] for a in disk_guards.disk_alerts(db)} == {"smart_danger", "usb2_slow"}


# --- B7 removal without eject ------------------------------------------------------------------------
def _report(db, *serials):
    from app.schemas.agent import AgentDiskReportCreate
    from app.services.disks import ingest_agent_disk_report

    disks = [{"serial_number": s, "display_name": s, "capacity_gb": 2000, "connected": True, "candidate_type": "usb"} for s in serials]
    with patch("app.services.disks.handle_disk_detected", lambda *a: None), patch("app.services.disk_guards.notify_unsafe_removal", create=True), patch(
        "app.services.notifications.notify_unsafe_removal", lambda *a: None
    ):
        return ingest_agent_disk_report(db, AgentDiskReportCreate(hostname="pve", observed_at=datetime.utcnow(), disks=disks))


def test_disk_seen_on_the_host_while_mounted_on_pbs_was_pulled_out(db):
    disk = _disk(db, handoff_status="attached_to_pbs", source="agent", reported_by_hostname="pve")
    _report(db, SERIAL)
    db.refresh(disk)
    assert disk.verify_recommended is True and disk.unsafe_removal_at is not None
    event = db.query(ActivityEvent).filter_by(action="unsafe_removal").one()
    assert event.details["reason"] == "mounted_on_pbs"
    assert [a["code"] for a in disk_guards.disk_alerts(db)] == ["verify_recommended"]
    _report(db, SERIAL)  # flagged once
    assert db.query(ActivityEvent).filter_by(action="unsafe_removal").count() == 1


def test_unplugged_during_a_run(db):
    disk = _disk(db, pbs_visible=False, pbs_handoff_slot=None, reported_by_hostname="pve", presence_state="present")
    _run(db, disk, status=BackupRunStatus.RUNNING)
    _report(db)  # the disk is gone
    db.refresh(disk)
    assert disk.verify_recommended is True


def test_normal_handoff_is_not_an_unsafe_removal(db):
    disk = _disk(db, handoff_status="attached_to_pbs", reported_by_hostname="pve")
    _run(db, disk, status=BackupRunStatus.RUNNING)  # the run handed it over
    _report(db, SERIAL)
    db.refresh(disk)
    assert not disk.verify_recommended


# --- B9 forgotten rotation, B8 USB 2.0 -----------------------------------------------------------------
def test_rotation_stale_after_n_days(db):
    disk = _disk(db)
    old = datetime.utcnow() - timedelta(days=40)
    _run(db, disk, status=BackupRunStatus.SUCCESS, started_at=old, finished_at=old)
    assert [a["code"] for a in disk_guards.disk_alerts(db, stale_days=30)] == ["rotation_stale"]
    assert disk_guards.disk_alerts(db, stale_days=60) == []
    update_settings(db, {"ROTATION_STALE_DAYS": 45})
    assert disk_guards.disk_alerts(db) == []


def test_usb2_is_a_warning_at_launch(db):
    from app.services.backup_readiness import external_backup_readiness

    disk = _disk(db, usb_speed_mbps=480)
    result = external_backup_readiness(db, disk)
    assert [w["code"] for w in result["warnings"]] == ["usb2_slow"]
    disk.usb_speed_mbps = 5000
    assert disk_profile.is_usb2(disk) is False


# --- A: identity, kind, contents, photos ---------------------------------------------------------------------
@pytest.mark.parametrize(
    ("kwargs", "kind"),
    [
        ({"rotational": True, "transport": "usb", "capacity_gb": 1863, "model": "WD My Passport 25E2"}, "hdd_portable"),
        ({"rotational": True, "transport": "usb", "capacity_gb": 7452, "model": "WD Elements 25A3"}, "hdd_desktop"),
        ({"rotational": True, "transport": "usb", "capacity_gb": 3726, "model": "WD My Book"}, "hdd_desktop"),
        ({"rotational": False, "transport": "usb", "capacity_gb": 931, "model": "Samsung PSSD T7"}, "ssd"),
        ({"rotational": False, "transport": "usb", "capacity_gb": 117, "model": "SanDisk Ultra Fit"}, "usb_stick"),
        ({"rotational": False, "transport": "usb", "capacity_gb": 1863, "model": "RTL9210B NVME"}, "nvme_enclosure"),
        ({"rotational": None, "transport": None, "capacity_gb": 500, "model": None}, "unknown"),
    ],
)
def test_disk_kind(kwargs, kind):
    assert disk_profile.disk_kind(**kwargs) == kind


def test_contents_summary_and_resources_view(db):
    from app.models import VirtualMachine
    from app.models.virtual_machine import VMType

    db.add(VirtualMachine(name="web", vm_type=VMType.VM, external_id="100", source="proxmox"))
    disk = _disk(db)
    snapshots = [
        {"backup-type": "vm", "backup-id": "100", "backup-time": T0, "size": 5 * GIB},
        {"backup-type": "vm", "backup-id": "100", "backup-time": T0 + DAY, "size": 6 * GIB},
    ]
    disk_contents.store_contents(db, disk, snapshots, now=datetime(2026, 11, 10))
    db.commit()
    (item,) = disk_contents.contents_payload(db, disk)["items"]
    assert (item["name"], item["versions"], item["size_bytes"], item["newest"]) == ("web", 2, 6 * GIB, "2023-11-15T22:13:20Z")
    copies = disk_contents.copies_by_group(db)["vm/100"]
    assert (copies[0]["disk_id"], copies[0]["newest"]) == (disk.id, "2023-11-15T22:13:20Z")


ADMIN = {"username": "admin", "password": "correct horse battery staple", "language": "fr", "timezone": "Europe/Paris"}


def _png(width=2400, height=1200) -> bytes:
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (width, height), (200, 30, 30)).save(out, format="PNG")
    return out.getvalue()


def test_photo_upload_is_resized_reencoded_and_deletable(client, session_factory, tmp_path, monkeypatch):
    from app.services import disk_photos

    monkeypatch.setattr(disk_photos, "data_dir", lambda: tmp_path)
    assert client.post("/api/v1/setup/admin", json=ADMIN).status_code == 201
    with session_factory() as db:
        disk_id = _disk(db).id
    response = client.put(f"/api/v1/disks/{disk_id}/photo", files={"file": ("disk.png", _png(), "image/png")})
    assert response.status_code == 200, response.text
    version = response.json()["photo_version"]
    stored = tmp_path / "disk-photos" / version
    from PIL import Image

    with Image.open(stored) as image:
        assert image.format == "WEBP" and max(image.size) == 1024
    photo = client.get(f"/api/v1/disks/{disk_id}/photo")
    assert photo.status_code == 200 and photo.headers["content-type"] == "image/webp"
    # A text file declared as JPEG is refused, the photo stays.
    bad = client.put(f"/api/v1/disks/{disk_id}/photo", files={"file": ("x.jpg", b"<svg onload=alert(1)>", "image/jpeg")})
    assert bad.status_code == 400 and bad.json()["detail"]["code"] == "photo_unreadable"
    big = client.put(f"/api/v1/disks/{disk_id}/photo", files={"file": ("x.png", b"0" * (5 * 1024 * 1024 + 1), "image/png")})
    assert big.status_code == 413 and big.json()["detail"]["code"] == "photo_too_large"
    assert client.delete(f"/api/v1/disks/{disk_id}/photo").json()["photo_version"] is None
    assert not stored.exists()
    assert client.get(f"/api/v1/disks/{disk_id}/photo").status_code == 404


def test_identity_and_retention_are_editable(client, session_factory):
    assert client.post("/api/v1/setup/admin", json=ADMIN).status_code == 201
    with session_factory() as db:
        disk_id = _disk(db).id
    response = client.patch(
        f"/api/v1/disks/{disk_id}",
        json={"display_name": "  Bureau  ", "color": "violet", "location_note": "Tiroir du bureau", "retention": {"keep_last": 6, "keep_monthly": 3}},
    )
    body = response.json()
    assert (body["display_name"], body["color"], body["location_note"]) == ("Bureau", "violet", "Tiroir du bureau")
    assert body["effective_retention"] == {"keep_last": 6, "keep_weekly": None, "keep_monthly": 3}
    assert client.patch(f"/api/v1/disks/{disk_id}", json={"color": "pink"}).status_code == 422
    empty = client.patch(f"/api/v1/disks/{disk_id}", json={"retention": {}})
    assert empty.status_code == 400 and empty.json()["detail"]["code"] == "retention_invalid"
    assert client.get("/api/v1/disks/alerts").status_code == 200
    assert client.get(f"/api/v1/disks/{disk_id}/contents").json()["items"] == []
