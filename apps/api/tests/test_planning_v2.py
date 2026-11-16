"""Phase 6: recurrences in the planning's time zone, every N weeks, rotation
over several disks, VM/CT selection, agenda, iCal export, Proxmox schedules."""

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

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
from app.models.backup_run import BackupRunStatus
from app.services import agenda, pve_schedule, recurrence
from app.services.calendar_feed import build_calendar, recurrence_rule, regenerate_calendar_token
from app.services.planning_scheduler import _ensure_run, group_filter_for, run_disk_serial

ADMIN = {"username": "admin", "password": "correct horse battery"}
NOW = datetime(2026, 11, 16, 12, 0)


def _event(db=None, recurrence_type=ScheduledBackupRecurrenceType.WEEKLY, start=datetime(2026, 11, 15, 8, 0), **overrides):
    values = dict(
        title="Rotation hebdo",
        enabled=True,
        disk_serial="WD-A",
        disk_label_or_model="WD 4 To",
        datastore="main",
        recurrence_type=recurrence_type,
        recurrence_config=None,
        timezone="Europe/Paris",
        window_starts_at=start,
        window_duration_minutes=300,
        notify_before_minutes=60,
        start_mode=ScheduledBackupStartMode.MANUAL_CONFIRMATION,
        auto_eject_after_success=False,
        created_at=NOW,
        updated_at=NOW,
    )
    values.update(overrides)
    event = ScheduledBackupEvent(**values)
    if db is not None:
        db.add(event)
        db.commit()
    return event


# --- Recurrences ------------------------------------------------------------------------


def test_weekly_keeps_the_local_time_across_daylight_saving():
    # Sunday 15 Nov 2026 09:00 Paris (UTC+1) = 08:00 UTC.
    event = _event()
    occurrences = recurrence.expand(event, datetime(2026, 11, 1), datetime(2027, 4, 30))
    assert occurrences[0] == datetime(2026, 11, 15, 8, 0)
    # After 29 March 2027 (UTC+2), still 09:00 in Paris = 07:00 UTC.
    summer = [moment for moment in occurrences if moment >= datetime(2027, 3, 29)]
    assert summer and all(moment.hour == 7 for moment in summer)
    assert all(recurrence.to_local(moment, recurrence._zone("Europe/Paris")).hour == 9 for moment in occurrences)


def test_every_two_weeks_and_occurrence_index():
    event = _event(recurrence_config={"interval": 2})
    occurrences = recurrence.expand(event, datetime(2026, 11, 1), datetime(2026, 12, 31))
    assert [moment.date().isoformat() for moment in occurrences] == ["2026-11-15", "2026-11-29", "2026-12-13", "2026-12-27"]
    assert [recurrence.occurrence_index(event, moment) for moment in occurrences] == [0, 1, 2, 3]
    assert recurrence.occurrence_index(event, datetime(2026, 11, 22, 8, 0)) is None  # an off week


def test_monthly_on_the_31st_falls_back_to_the_last_day():
    event = _event(recurrence_type=ScheduledBackupRecurrenceType.MONTHLY, start=datetime(2027, 1, 31, 5, 0), timezone="UTC")
    days = [moment.day for moment in recurrence.expand(event, datetime(2027, 1, 1), datetime(2027, 6, 1))]
    assert days == [31, 28, 31, 30, 31]
    assert recurrence_rule(event) == "FREQ=MONTHLY;BYMONTHDAY=28,29,30,31;BYSETPOS=-1"


def test_rrule_carries_the_interval():
    assert recurrence_rule(_event(recurrence_config={"interval": 3})) == "FREQ=WEEKLY;INTERVAL=3"
    assert recurrence_rule(_event(recurrence_type=ScheduledBackupRecurrenceType.DAILY)) == "FREQ=DAILY"


# --- Rotation ------------------------------------------------------------------------------


def test_rotation_alternates_disks_in_order(db):
    event = _event(db, rotation_disk_serials=["WD-A", "TOSH-B"])
    occurrences = recurrence.expand(event, datetime(2026, 11, 1), datetime(2026, 12, 10))
    assert [recurrence.disk_serial_for(event, moment) for moment in occurrences] == ["WD-A", "TOSH-B", "WD-A", "TOSH-B"]
    run = _ensure_run(db, event, occurrences[1])
    db.commit()
    assert run.disk_serial == "TOSH-B" and run_disk_serial(event, run) == "TOSH-B"


def test_planning_api_validates_rotation_vm_groups_and_interval(client, db):
    assert client.post("/api/v1/setup/admin", json=ADMIN).status_code == 201
    payload = {
        "title": "Rotation hebdo",
        "disk_serial": "ignored-when-rotation-is-set",
        "rotation_disk_serials": ["WD-A", "TOSH-B", "WD-A"],
        "datastore": "main",
        "recurrence_type": "weekly",
        "recurrence_config": {"interval": 2},
        "timezone": "Europe/Paris",
        "window_starts_at": "2026-11-22T08:00:00Z",
        "vm_groups": ["vm/100", "CT/101"],
    }
    created = client.post("/api/v1/planning/events", json=payload)
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["disk_serial"] == "WD-A"
    assert body["rotation_disk_serials"] == ["WD-A", "TOSH-B"]
    assert body["vm_groups"] == ["ct/101", "vm/100"]
    bad = client.post("/api/v1/planning/events", json={**payload, "vm_groups": ["vm/abc"]})
    assert bad.status_code == 422 and bad.json()["detail"]["code"] == "planning_vm_group_invalid"
    bad = client.post("/api/v1/planning/events", json={**payload, "recurrence_config": {"interval": 99}})
    assert bad.status_code == 422 and bad.json()["detail"]["code"] == "planning_interval_invalid"


def test_vm_groups_become_a_pbs_group_filter():
    assert group_filter_for(["vm/100", "ct/101"]) == ["group:vm/100", "group:ct/101"]
    assert group_filter_for([]) is None and group_filter_for(None) is None


def test_group_filter_reaches_the_pbs_sync_job():
    from app.services.pbs_client import PBSClient

    sent = {}
    client = PBSClient.__new__(PBSClient)
    client._request = lambda method, path, **kwargs: sent.update(kwargs.get("body") or {})  # type: ignore[method-assign]
    client.create_local_sync_job(job_id="pbo-1", store="pbo-a", remote_store="main", group_filter=["group:vm/100"])
    assert sent["group-filter"] == ["group:vm/100"]


# --- Agenda ----------------------------------------------------------------------------------


def test_agenda_lists_planned_occurrences_manual_runs_and_states(db):
    disk = ExternalDisk(serial_number="WD-A", display_name="WD 4 To", capacity_gb=3726, connected=True, trusted=True, source="agent")
    db.add(disk)
    db.commit()
    event = _event(db, start=datetime(2026, 11, 8, 8, 0))
    # Last week's occurrence: started a backup that succeeded.
    backup = ExternalBackupRun(
        disk_id=disk.id, status=BackupRunStatus.SUCCESS, started_at=datetime(2026, 11, 15, 8, 5),
        finished_at=datetime(2026, 11, 15, 9, 30), target_path="/x", datastore_name="main",
        mode=ExternalBackupMode.DEDICATED, created_at=NOW,
    )
    manual = ExternalBackupRun(
        disk_id=disk.id, status=BackupRunStatus.RUNNING, started_at=datetime(2026, 11, 16, 11, 0),
        target_path="/x", datastore_name="main", mode=ExternalBackupMode.DEDICATED, created_at=NOW, progress_percent=42.0,
    )
    db.add_all([backup, manual])
    db.commit()
    db.add(ScheduledBackupRun(
        event_id=event.id, scheduled_for=datetime(2026, 11, 15, 8, 0), window_starts_at=datetime(2026, 11, 15, 8, 0),
        window_ends_at=datetime(2026, 11, 15, 13, 0), status=ScheduledBackupRunStatus.SUCCESS, backup_run_id=backup.id,
        created_at=NOW, updated_at=NOW,
    ))
    db.commit()
    result = agenda.build_agenda(db, datetime(2026, 11, 1), datetime(2026, 11, 30), include_proxmox=False, zone_name="Europe/Paris", now=NOW)
    items = result["items"]
    states = [(item["kind"], item["scheduled_for"] or item["starts_at"], item["status"]) for item in items]
    assert ("external", "2026-11-08T08:00:00Z", "missed") in states
    assert ("external", "2026-11-15T08:00:00Z", "success") in states
    assert ("external", "2026-11-22T08:00:00Z", "planned") in states
    running = next(item for item in items if item["kind"] == "manual")
    assert running["status"] == "running" and running["backup_run_id"] == manual.id and running["progress_percent"] == 42.0
    linked = next(item for item in items if item["status"] == "success")
    assert linked["backup_run_id"] == backup.id and linked["disk_label"] == "WD 4 To"


def test_agenda_shows_runs_waiting_for_confirmation(db):
    event = _event(db, start=datetime(2026, 11, 16, 11, 0))
    db.add(ScheduledBackupRun(
        event_id=event.id, scheduled_for=datetime(2026, 11, 16, 11, 0), window_starts_at=datetime(2026, 11, 16, 11, 0),
        window_ends_at=datetime(2026, 11, 16, 16, 0), status=ScheduledBackupRunStatus.WAITING_FOR_CONFIRMATION,
        created_at=NOW, updated_at=NOW,
    ))
    db.commit()
    items = agenda.build_agenda(db, datetime(2026, 11, 16), datetime(2026, 11, 17), include_proxmox=False, zone_name="UTC", now=NOW)["items"]
    assert [item["status"] for item in items] == ["to_confirm"]
    assert items[0]["planning_run_id"] is not None


def test_agenda_route_and_proxmox_jobs(client, db, monkeypatch):
    assert client.post("/api/v1/setup/admin", json=ADMIN).status_code == 201
    agenda.reset_proxmox_cache()
    monkeypatch.setattr(agenda, "_proxmox_jobs", lambda: ([{"id": "backup-1", "schedule": "sun 01:00", "storage": "pbs8to"}, {"id": "odd", "schedule": "*/5"}], None))
    response = client.get("/api/v1/planning/agenda", params={"start": "2026-11-16T00:00:00Z", "end": "2026-11-23T00:00:00Z", "proxmox": "true"})
    assert response.status_code == 200, response.text
    body = response.json()
    pve = [item for item in body["items"] if item["kind"] == "proxmox"]
    assert len(pve) == 1 and pve[0]["read_only"] is True and pve[0]["disk_label"] == "pbs8to"
    assert body["proxmox_unsupported"] == ["odd"]
    bad = client.get("/api/v1/planning/agenda", params={"start": "2026-11-16T00:00:00Z", "end": "2026-11-01T00:00:00Z"})
    assert bad.status_code == 400


# --- Proxmox schedules -------------------------------------------------------------------


@pytest.mark.parametrize(
    "schedule, weekdays, times",
    [
        ("21:00", (0, 1, 2, 3, 4, 5, 6), ((21, 0),)),
        ("sun 01:00", (6,), ((1, 0),)),
        ("mon..fri 22:30", (0, 1, 2, 3, 4), ((22, 30),)),
        ("sat..mon 3:00", (0, 5, 6), ((3, 0),)),
        ("mon,wed 02:00,14:30", (0, 2), ((2, 0), (14, 30))),
        ("daily", (0, 1, 2, 3, 4, 5, 6), ((0, 0),)),
        ("*-*-* 02:30", (0, 1, 2, 3, 4, 5, 6), ((2, 30),)),
    ],
)
def test_pve_schedules(schedule, weekdays, times):
    parsed = pve_schedule.parse(schedule)
    assert parsed is not None and parsed.weekdays == weekdays and parsed.times == times


@pytest.mark.parametrize("schedule", ["", "*/5", "mon 25:00", "someday 01:00", "2026-01-01 02:00 extra"])
def test_unsupported_pve_schedules(schedule):
    assert pve_schedule.parse(schedule) is None


def test_pve_occurrences_in_local_time():
    parsed = pve_schedule.parse("sun 01:00")
    moments = pve_schedule.occurrences(parsed, datetime(2026, 11, 1), datetime(2026, 11, 30), "Europe/Paris")
    assert [moment.isoformat() for moment in moments] == [
        "2026-11-01T00:00:00", "2026-11-08T00:00:00", "2026-11-15T00:00:00", "2026-11-22T00:00:00", "2026-11-29T00:00:00",
    ]
    assert parsed.rrule() == "FREQ=WEEKLY;BYDAY=SU;BYHOUR=1;BYMINUTE=0"


# --- iCal export -------------------------------------------------------------------------------


def test_ics_export_options(client, db, monkeypatch, secret_key):
    _event(db, rotation_disk_serials=["WD-A", "TOSH-B"], recurrence_config={"interval": 2})
    token = regenerate_calendar_token(db)
    monkeypatch.setattr(agenda, "_proxmox_jobs", lambda: ([{"id": "backup-1", "schedule": "sun 01:00", "comment": "Nuit"}], None))
    plain = client.get(f"/calendar.ics?token={token}")
    text = plain.text.replace("\r\n ", "")
    assert "RRULE:FREQ=WEEKLY;INTERVAL=2" in text
    assert "WD-A → TOSH-B" in text
    assert "pbo-pve-" not in text
    assert plain.headers["content-disposition"].startswith("inline")
    with_pve = client.get(f"/calendar.ics?token={token}&proxmox=true&download=true")
    assert "UID:pbo-pve-backup-1" in with_pve.text and "RRULE:FREQ=WEEKLY;BYDAY=SU;BYHOUR=1;BYMINUTE=0" in with_pve.text
    assert with_pve.headers["content-disposition"].startswith("attachment")


def test_build_calendar_keeps_working_without_proxmox(db, monkeypatch):
    monkeypatch.setattr(agenda, "_proxmox_jobs", lambda: ([], "PVE unreachable"))
    text = build_calendar(db, include_proxmox=True, zone_name="Europe/Paris", now=NOW)
    assert "END:VCALENDAR" in text and "pbo-pve-" not in text


def test_run_kind_backup_vs_eject():
    from app.services.run_log import run_kind

    disk = SimpleNamespace(pbs_datastore_name="pbo-a")
    assert run_kind(SimpleNamespace(datastore_name="main", target_path="/x", auto_eject_after_success=False), disk) == "backup"
    assert run_kind(SimpleNamespace(datastore_name="pbo-a", target_path="/x", auto_eject_after_success=False), disk) == "eject"
    assert run_kind(SimpleNamespace(datastore_name="pbo-a", target_path="/x", auto_eject_after_success=True), disk) == "backup"
    _ = timedelta  # keep the import list stable
