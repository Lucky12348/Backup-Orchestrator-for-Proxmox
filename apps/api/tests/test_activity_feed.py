"""Phase 6: the Activity page list (backups, ejects, detections, planning)."""

from datetime import datetime, timedelta

from app.models import (
    ExternalBackupMode,
    ExternalBackupRun,
    ExternalDisk,
    ScheduledBackupEvent,
    ScheduledBackupRecurrenceType,
    ScheduledBackupRun,
    ScheduledBackupRunStatus,
    ScheduledBackupStartMode,
)
from app.models.activity_event import ActivityEvent
from app.models.backup_run import BackupRunStatus
from app.services.activity_feed import build_activity

NOW = datetime(2026, 11, 16, 12, 0)
ADMIN = {"username": "admin", "password": "correct horse battery"}
SUMMARY = "[2026-11-15T09:00:00Z] sync_job 2026-11-15T10:00:00+01:00: Summary: sync job found no new data to push\n"


def _seed(db):
    disk = ExternalDisk(serial_number="WD-A", display_name="WD 4 To", capacity_gb=3726, connected=True, trusted=True,
                        source="agent", pbs_datastore_name="pbo-wd-a")
    db.add(disk)
    db.commit()
    backup = ExternalBackupRun(disk_id=disk.id, status=BackupRunStatus.SUCCESS, started_at=NOW - timedelta(days=1),
                               finished_at=NOW - timedelta(days=1) + timedelta(minutes=12), target_path="/x",
                               datastore_name="main", mode=ExternalBackupMode.DEDICATED, created_at=NOW, stdout_log=SUMMARY,
                               auto_eject_after_success=True)
    eject = ExternalBackupRun(disk_id=disk.id, status=BackupRunStatus.FAILED, started_at=NOW - timedelta(hours=2),
                              target_path="/mnt/pbo", datastore_name="pbo-wd-a", mode=ExternalBackupMode.DEDICATED,
                              created_at=NOW, message="Disk busy", current_step="eject_not_finished")
    old = ExternalBackupRun(disk_id=disk.id, status=BackupRunStatus.SUCCESS, started_at=NOW - timedelta(days=200),
                            target_path="/x", datastore_name="main", mode=ExternalBackupMode.DEDICATED, created_at=NOW)
    db.add_all([backup, eject, old])
    db.add(ActivityEvent(created_at=NOW - timedelta(hours=3), category="disk", action="usb_added", status="info",
                         summary="USB disk WD-A plugged in on pve", details={"serial": "WD-A", "model": "WD"}))
    event = ScheduledBackupEvent(title="Rotation", enabled=True, disk_serial="WD-A", datastore="main",
                                 recurrence_type=ScheduledBackupRecurrenceType.WEEKLY, timezone="UTC",
                                 window_starts_at=NOW - timedelta(days=7), window_duration_minutes=60, notify_before_minutes=0,
                                 start_mode=ScheduledBackupStartMode.MANUAL_CONFIRMATION, created_at=NOW, updated_at=NOW)
    db.add(event)
    db.commit()
    db.add(ScheduledBackupRun(event_id=event.id, scheduled_for=NOW - timedelta(days=7), window_starts_at=NOW - timedelta(days=7),
                              window_ends_at=NOW - timedelta(days=7) + timedelta(hours=1), status=ScheduledBackupRunStatus.MISSED,
                              disk_serial="WD-A", created_at=NOW, updated_at=NOW))
    db.commit()
    return disk, backup, eject


def test_activity_lists_every_kind_newest_first(db):
    disk, backup, eject = _seed(db)
    items = build_activity(db, days=90, now=NOW)
    assert [(item["type"], item["status"]) for item in items] == [
        ("eject", "failed"),
        ("detection", "plugged"),
        ("backup", "success"),
        ("planning", "missed"),
    ]
    first_backup = items[2]
    assert first_backup["run_id"] == backup.id and first_backup["duration_seconds"] == 720
    assert first_backup["no_new_data"] is True and first_backup["auto_eject"] is True
    assert items[0]["step"] == "eject_not_finished" and items[0]["message"] == "Disk busy"
    assert items[1]["disk_id"] == disk.id and items[1]["disk_label"] == "WD 4 To"
    assert items[3]["event_title"] == "Rotation"
    assert len(build_activity(db, days=366, now=NOW)) == 5  # the 200-day-old run


def test_activity_route_requires_a_session(client):
    assert client.get("/api/v1/activity-events/feed").status_code == 401
    assert client.post("/api/v1/setup/admin", json=ADMIN).status_code == 201
    response = client.get("/api/v1/activity-events/feed", params={"days": 30})
    assert response.status_code == 200 and response.json() == []
