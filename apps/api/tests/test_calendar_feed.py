"""iCalendar feed of the planning (`/calendar.ics?token=…`)."""

from datetime import datetime, timedelta

import pytest

from app.models import (
    ExternalBackupMode,
    ExternalBackupRun,
    ExternalDisk,
    ScheduledBackupEvent,
    ScheduledBackupRecurrenceType,
    ScheduledBackupStartMode,
)
from app.models.backup_run import BackupRunStatus
from app.services.calendar_feed import (
    _fold,
    build_calendar,
    recurrence_rule,
    regenerate_calendar_token,
)
from app.services.planning_scheduler import expand_occurrences

ADMIN = {"username": "admin", "password": "correct horse battery"}
NOW = datetime(2026, 11, 15, 12, 0, 0)


def _event(db, recurrence, start, **overrides):
    values = dict(
        title="Hebdo, disque A",
        enabled=True,
        disk_serial="SER-A",
        disk_label_or_model="Disque A",
        datastore="main",
        recurrence_type=recurrence,
        timezone="Europe/Paris",
        window_starts_at=start,
        window_duration_minutes=120,
        notify_before_minutes=60,
        start_mode=ScheduledBackupStartMode.MANUAL_CONFIRMATION,
        auto_eject_after_success=True,
        created_at=NOW,
        updated_at=NOW,
    )
    values.update(overrides)
    event = ScheduledBackupEvent(**values)
    db.add(event)
    db.commit()
    return event


def _unfold(text: str) -> str:
    return text.replace("\r\n ", "")


def test_feed_requires_a_valid_token(client, db, secret_key):
    assert client.get("/calendar.ics").status_code == 404
    assert client.get("/calendar.ics?token=whatever").status_code == 404
    token = regenerate_calendar_token(db)
    assert client.get(f"/calendar.ics?token={token}").status_code == 200
    assert client.get("/calendar.ics?token=whatever").status_code == 404


def test_token_management_api(client):
    assert client.get("/api/v1/calendar").status_code == 401
    assert client.post("/api/v1/setup/admin", json=ADMIN).status_code == 201
    assert client.get("/api/v1/calendar").json() == {"enabled": False, "url": None}

    first = client.post("/api/v1/calendar/token").json()
    assert first["enabled"] is True
    assert first["url"].startswith("https://testserver/calendar.ics?token=")
    assert client.get("/api/v1/calendar").json() == first

    second = client.post("/api/v1/calendar/token").json()
    assert second["url"] != first["url"]
    old_token = first["url"].split("token=")[1]
    assert client.get(f"/calendar.ics?token={old_token}").status_code == 404
    assert client.get(second["url"].replace("https://testserver", "")).status_code == 200

    assert client.delete("/api/v1/calendar/token").json() == {"enabled": False, "url": None}
    assert client.get(second["url"].replace("https://testserver", "")).status_code == 404


def test_token_is_stored_encrypted(db, secret_key):
    from app.models.app_setting import AppSetting

    token = regenerate_calendar_token(db)
    row = db.get(AppSetting, "meta.calendar_token")
    assert row.is_secret and token not in row.value


def test_recurring_events_are_rrules(client, db, secret_key):
    _event(db, ScheduledBackupRecurrenceType.WEEKLY, datetime(2026, 11, 20, 18, 30))
    _event(db, ScheduledBackupRecurrenceType.DAILY, datetime(2026, 11, 16, 2, 0), title="Quotidien")
    _event(db, ScheduledBackupRecurrenceType.ONCE, datetime(2026, 12, 1, 9, 0), title="Ponctuel")
    _event(db, ScheduledBackupRecurrenceType.WEEKLY, datetime(2026, 11, 16, 2, 0), title="Désactivé", enabled=False)
    _event(db, ScheduledBackupRecurrenceType.WEEKLY, datetime(2026, 11, 16, 2, 0), title="Supprimé", deleted_at=NOW)
    token = regenerate_calendar_token(db)

    response = client.get(f"/calendar.ics?token={token}")
    assert response.headers["content-type"].startswith("text/calendar")
    text = _unfold(response.text)
    assert text.startswith("BEGIN:VCALENDAR\r\nVERSION:2.0\r\n")
    assert text.endswith("END:VCALENDAR\r\n")
    assert text.count("BEGIN:VEVENT") == 3
    assert "SUMMARY:Hebdo\\, disque A" in text
    # Recurring: the local time of the planning's zone (18:30 UTC = 19:30 Paris).
    assert "DTSTART;TZID=Europe/Paris:20261120T193000" in text and "DTEND;TZID=Europe/Paris:20261120T213000" in text
    assert "DTSTART:20261201T090000Z" in text  # one-off: UTC
    assert "RRULE:FREQ=WEEKLY" in text and "RRULE:FREQ=DAILY" in text
    assert text.count("RRULE:") == 2  # the one-off event has none
    assert "Désactivé" not in text and "Supprimé" not in text
    assert "Éjection automatique après succès" in text
    for line in response.text.split("\r\n"):
        assert len(line.encode("utf-8")) <= 75


@pytest.mark.parametrize("day", [5, 28, 29, 30, 31])
def test_monthly_rule_matches_the_scheduler(db, day):
    event = ScheduledBackupEvent(
        recurrence_type=ScheduledBackupRecurrenceType.MONTHLY,
        window_starts_at=datetime(2027, 1, day, 6, 0),
        deleted_at=None,
        id=1,
    )
    rule = recurrence_rule(event)
    scheduler_days = [
        occurrence.day for occurrence in expand_occurrences(event, datetime(2027, 1, 1), datetime(2029, 1, 1))
    ]
    expected = [_rrule_day(rule, year, month) for year in (2027, 2028) for month in range(1, 13)]
    assert scheduler_days == expected


def _rrule_day(rule: str, year: int, month: int) -> int:
    """Tiny evaluator for the two monthly forms we emit."""
    import calendar

    params = dict(part.split("=") for part in rule.split(";"))
    days = [int(d) for d in params["BYMONTHDAY"].split(",")]
    last = calendar.monthrange(year, month)[1]
    valid = [d for d in days if d <= last]
    return valid[-1] if params.get("BYSETPOS") == "-1" else valid[0]


def test_history_is_optional(client, db, secret_key):
    disk = ExternalDisk(serial_number="SER-A", display_name="Disque A", capacity_gb=1000, connected=False)
    db.add(disk)
    db.commit()
    started = datetime.utcnow() - timedelta(days=2)
    db.add(
        ExternalBackupRun(
            disk_id=disk.id,
            status=BackupRunStatus.SUCCESS,
            started_at=started,
            finished_at=started + timedelta(hours=1),
            target_path="/x",
            datastore_name="pbo-a",
            message="Export terminé",
            mode=ExternalBackupMode.DEDICATED,
            created_at=started,
        )
    )
    db.add(
        ExternalBackupRun(
            disk_id=disk.id,
            status=BackupRunStatus.FAILED,
            started_at=datetime.utcnow() - timedelta(days=200),
            target_path="/x",
            datastore_name="pbo-a",
            mode=ExternalBackupMode.DEDICATED,
            created_at=started,
        )
    )
    db.commit()
    token = regenerate_calendar_token(db)

    assert "pbo-run-" not in client.get(f"/calendar.ics?token={token}").text
    text = _unfold(client.get(f"/calendar.ics?token={token}&history=true").text)
    assert text.count("pbo-run-") == 1
    assert "Sauvegarde externe Disque A : success" in text
    text = _unfold(client.get(f"/calendar.ics?token={token}&history=true&history_days=366").text)
    assert text.count("pbo-run-") == 2


def test_fold_keeps_multibyte_characters_intact():
    line = "SUMMARY:" + "é" * 80
    folded = _fold(line)
    assert all(len(part.encode("utf-8")) <= 75 for part in folded.split("\r\n"))
    assert folded.replace("\r\n ", "") == line


def test_build_calendar_without_events(db):
    text = build_calendar(db, now=NOW)
    assert "BEGIN:VEVENT" not in text and "X-WR-CALNAME" in text
