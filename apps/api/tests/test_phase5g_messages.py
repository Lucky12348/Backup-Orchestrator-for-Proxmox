"""Phase 5g: exact messages (B37/B39), launch dialog (B48), rejected disks."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from app.models import ExternalDisk
from app.services import capacity, disk_events, run_log
from app.services.disks import apply_disk_decision
from app.services.pbs_api_backend import gc_removed_bytes, retention_message
from app.services.pbs_guards import GuardViolation

T = [int(datetime(2026, 11, 17, h, tzinfo=timezone.utc).timestamp()) for h in range(1, 6)]


def _snap(group, when, size=2**30):
    kind, ident = group.split("/")
    return {"backup-type": kind, "backup-id": ident, "backup-time": when, "size": size}


# --- B37: versions copied vs data transferred ---------------------------------------------------


def test_versions_copied_are_counted_apart_from_the_bytes():
    log = "\n".join(
        [
            "[2026-11-17T01:00:00Z] sync_job 2026-11-17T02:00:00+01:00: [ct/101]: sync snapshot 2026-11-16T03:00:00Z",
            "[2026-11-17T01:00:00Z] sync_job 2026-11-17T02:00:00+01:00: [ct/101]: sync snapshot 2026-11-16T04:00:00Z",
            "[2026-11-17T01:00:00Z] sync_job 2026-11-17T02:00:00+01:00: [ct/102]: re-sync snapshot 2026-11-16T04:00:00Z",
            "[2026-11-17T01:00:01Z] sync_job 2026-11-17T02:00:01+01:00: Summary: sync job found no new data to pull",
        ]
    )
    summary = run_log.transfer_summary(log)
    assert (summary["versions_copied"], summary["transferred_bytes"], summary["no_new_data"]) == (2, 0, True)


def test_a_zero_rate_is_not_reported():
    log = "2026-11-17T02:00:01+01:00: Summary: sync job pulled 0 B in 0 chunks (average rate: 0 B/s)"
    assert run_log.transfer_summary(log)["rate_bytes_per_second"] is None


# --- B39: "space freed" only when the GC freed some ----------------------------------------------


def test_retention_message_depends_on_what_the_gc_freed():
    assert gc_removed_bytes(["Removed garbage: 1.5 GiB", "Removed chunks: 12"]) == int(1.5 * 2**30)
    assert gc_removed_bytes(["starting garbage collection"]) is None
    cases = {
        (2, 3 * 2**20): "cleanup_freed",
        (2, 0): "cleanup_pending",
        (2, None): "cleanup_pending",
        (0, None): "cleanup_nothing",
        (None, None): "cleanup_done",
    }
    for (removed, freed), code in cases.items():
        entry = run_log.build_entries(f"[2026-11-17T01:00:00Z] cleanup {retention_message('pbo-a', 'keep-last=2', removed, freed)}")[0]
        assert entry.code == code, (removed, freed)
    freed = run_log.build_entries(f"[2026-11-17T01:00:00Z] cleanup {retention_message('pbo-a', 'keep-last=2', 2, 1024)}")[0]
    assert freed.params == {"store": "pbo-a", "options": "keep-last=2", "removed": "2", "freed_bytes": "1024"}


# --- B48: launch dialog --------------------------------------------------------------------------


def test_versions_removed_by_the_disk_retention_are_not_new():
    """The disk kept the 2 newest versions; PBS skips the older ones."""
    source = [_snap("ct/101", when) for when in T[:4]]
    disk_keys = {f"ct/101/{T[2]}", f"ct/101/{T[3]}"}
    assert capacity.estimate_bytes(source, disk_keys) == {"needed_bytes": 0, "new_snapshots": 0, "new_groups": 0}
    newer = capacity.estimate_bytes([*source, _snap("ct/101", T[4], size=100)], disk_keys)
    assert (newer["new_snapshots"], newer["needed_bytes"]) == (1, 10)


def test_unknown_disk_contents_give_no_estimate(db):
    disk = ExternalDisk(serial_number="S1", display_name="d", capacity_gb=500, source="agent", trusted=True,
                        prepared_as_pbs_datastore=True, pbs_datastore_name="pbo-s1", filesystem_free_gb=100)
    db.add(disk)
    db.commit()
    client = SimpleNamespace(list_snapshots=lambda store: [_snap("ct/101", T[0])])
    settings = SimpleNamespace(pbs_datastore="main", pbs_mode="api", pbs_api_url="https://pbs:8007")
    capacity.reset_cache()
    with patch("app.services.settings_service.effective_settings", lambda db: settings):
        estimate = capacity.check_space(db, disk, client=client)
    assert (estimate["contents_known"], estimate["known"], estimate["sufficient"]) == (False, False, None)


def test_the_preview_target_is_the_disk_datastore(db):
    from app.services.external_backups import get_external_backup_preview

    disk = ExternalDisk(serial_number="PBOTESTA", display_name="d", capacity_gb=500, source="agent", trusted=True,
                        dedicated_backup_disk=True, pbs_datastore_name="pbo-pbotesta")
    db.add(disk)
    db.commit()
    settings = SimpleNamespace(pbs_mode="api", external_backup_legacy_coexistence_enabled=False, external_loop_datastore_size_gb=500)
    with patch("app.services.external_backups.get_settings", lambda: settings):
        preview = get_external_backup_preview(db, disk.id)
    assert (preview["target_path"], preview["target_datastore"]) == ("/mnt/datastore/pbo-pbotesta", "pbo-pbotesta")


# --- rejected disks --------------------------------------------------------------------------------


def _small(db, gib=16):
    disk = ExternalDisk(serial_number=f"SMALL{gib}", display_name="Petit", capacity_gb=gib, source="agent", connected=True)
    db.add(disk)
    db.commit()
    return disk


def test_the_minimum_size_is_a_setting(db):
    disk = _small(db, 16)
    assert disk_events.reject_reason(disk) == "too_small" and disk.min_size_gb == 32
    with patch("app.services.disk_events.min_disk_size_gib", lambda: 8):
        assert disk_events.reject_reason(disk) is None


def test_approve_anyway_needs_the_name_and_lifts_the_guard(db):
    disk = _small(db)
    with pytest.raises(HTTPException) as plain:
        apply_disk_decision(db, disk.id, "approve", "Petit")
    assert plain.value.detail["code"] == "decision_disk_unusable"
    with pytest.raises(HTTPException) as unnamed:
        apply_disk_decision(db, disk.id, "approve_anyway", "x")
    assert unnamed.value.detail["code"] == "decision_name_mismatch"
    with patch("app.services.disk_events.publish_report_events", lambda *a: None):
        disk = apply_disk_decision(db, disk.id, "approve_anyway", "Petit")
    assert (disk.trusted, disk.operator_decision, disk_events.reject_reason(disk), disk.status) == (True, "approved_small", None, "known")


def test_a_zero_byte_disk_is_never_approved(db):
    disk = _small(db, 0)
    with pytest.raises(HTTPException):
        apply_disk_decision(db, disk.id, "approve_anyway", "Petit")


def test_hide_and_show_again(db):
    disk = _small(db)
    with patch("app.services.disk_events.publish_report_events", lambda *a: None):
        assert apply_disk_decision(db, disk.id, "hide", None).status == "hidden"
        assert apply_disk_decision(db, disk.id, "clear", None).status == "rejected"


def test_the_pbs_guard_follows_the_approval():
    from app.services.pbs_guards import plan_dedicated_preparation

    record = SimpleNamespace(trusted=True, prepared=False)
    pbs_disk = {"name": "sdb", "size": 16 * 2**30, "used": "unused", "partitions": []}
    common = dict(serial="S", disk_record=record, pbs_disk=pbs_disk, datastore_configs=[], target_name="pbo-s", confirmation=True, running_tasks=[])
    with pytest.raises(GuardViolation) as error:
        plan_dedicated_preparation(**common)
    assert error.value.code == "disk_too_small"
    assert plan_dedicated_preparation(**common, min_size_gib=1).action == "format"
