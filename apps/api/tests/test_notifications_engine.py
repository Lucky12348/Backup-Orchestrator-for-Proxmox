"""Phase 5j: notification channels (Apprise), engine, migration, retention."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import apprise
import pytest

from app.core.config import Settings
from app.core.security import encrypt_value
from app.models import (
    AppSetting,
    BackupRunStatus,
    ExternalBackupMode,
    ExternalBackupRun,
    ExternalDisk,
    NotificationChannel,
    NotificationDelivery,
    NotificationPreferences,
)
from app.services import notification_catalog as catalog
from app.services import notification_engine as engine
from app.services import run_retention
from app.services.notification_channels import (
    ChannelError,
    apply_payload,
    apprise_url,
    read_secrets,
)
from app.services.notification_migration import MARKER_KEY, migrate_legacy_ntfy

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)


@pytest.fixture
def sent(monkeypatch, secret_key):
    """Every message that would leave PBO: (url, title, body, type)."""
    calls: list[tuple[str, str, str, str]] = []
    outcome = {"ok": True, "error": None}

    def fake(url, title, body, notify_type):
        calls.append((url, title, body, notify_type))
        return outcome["ok"], outcome["error"]

    monkeypatch.setattr(engine, "_apprise_notify", fake)
    monkeypatch.setattr(engine, "_now", lambda: NOW - timedelta(minutes=1))
    monkeypatch.setattr(engine, "get_settings", lambda: Settings(public_url="https://pbo.lan", timezone="Europe/Paris"))
    calls_obj = SimpleNamespace(calls=calls, outcome=outcome)
    return calls_obj


def _channel(db, kind="ntfy", **extra):
    payloads = {
        "ntfy": {"config": {"server_url": "https://ntfy.example.org", "topic": "pbo-alerts"}, "secrets": {"token": "tk_0123456789"}},
        "gotify": {"config": {"server_url": "https://gotify.lan:8443/sub"}, "secrets": {"app_token": "AbCdEf123"}},
        "discord": {"config": {}, "secrets": {"webhook_url": "https://discord.com/api/webhooks/123456/abc_DEF-ghi"}},
        "telegram": {"config": {"chat_id": "-100123456"}, "secrets": {"bot_token": "123456:ABCdefGHIjklMNOpqrSTUvwxYZ"}},
        "email": {
            "config": {"host": "smtp.example.org", "security": "starttls", "username": "pbo", "from_address": "pbo@example.org", "to_addresses": "me@example.org, you@example.org"},
            "secrets": {"password": "s3cr3t-pass"},
        },
        "webhook": {"config": {"url": "https://hooks.lan/pbo", "header_names": ["Authorization"]}, "secrets": {"header.Authorization": "Bearer xyz-secret"}},
        "apprise": {"config": {}, "secrets": {"url": "json://hooks.lan/apprise"}},
    }
    payload = {"name": f"{kind} channel", "kind": kind, **payloads[kind], **extra}
    channel = apply_payload(NotificationChannel(), payload, creating=True)
    db.add(channel)
    db.commit()
    return channel


# --- channels --------------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["ntfy", "gotify", "discord", "telegram", "email", "webhook", "apprise"])
def test_every_channel_builds_a_url_apprise_accepts_and_sends(db, sent, kind):
    channel = _channel(db, kind)
    url = apprise_url(channel, read_secrets(channel), level="error", link="https://pbo.lan/disks/1", extras={"event": "run_failed"})
    assert apprise.Apprise().add(url), url
    result = engine.send_test(db, channel)
    assert result == {"ok": True, "error": None}
    assert len(sent.calls) == 1 and sent.calls[0][1] == "PBO test notification"


def test_channel_specific_urls(db, secret_key):
    ntfy = _channel(db, "ntfy")
    url = apprise_url(ntfy, read_secrets(ntfy), level="error", link="https://pbo.lan/x")
    assert url.startswith("ntfys://ntfy.example.org/pbo-alerts?") and "token=tk_0123456789" in url and "priority=max" in url and "click=" in url
    assert apprise_url(_channel(db, "discord"), {"webhook_url": "https://discord.com/api/webhooks/123456/abc_DEF-ghi"}) == "discord://123456/abc_DEF-ghi"
    telegram = _channel(db, "telegram")
    assert apprise_url(telegram, read_secrets(telegram)).startswith("tgram://123456%3AABC")
    email = _channel(db, "email")
    url = apprise_url(email, read_secrets(email))
    assert url.startswith("mailtos://pbo:s3cr3t-pass@smtp.example.org:587?") and "mode=starttls" in url and "to=me%40example.org%2Cyou%40example.org" in url
    webhook = _channel(db, "webhook")
    url = apprise_url(webhook, read_secrets(webhook), extras={"event": "disk_new", "link": "https://pbo.lan/disks/2"})
    assert url.startswith("jsons://hooks.lan/pbo?") and "%3Aevent=disk_new" in url
    # Apprise reads the header back with its space (a "+" would stay a "+").
    assert apprise.Apprise.instantiate(url).headers["Authorization"] == "Bearer xyz-secret"


@pytest.mark.parametrize(
    ("kind", "change", "code"),
    [
        ("ntfy", {"config": {"topic": "bad topic!"}}, "invalid_topic"),
        ("gotify", {"config": {"server_url": "ftp://x"}}, "invalid_url"),
        ("discord", {"secrets": {"webhook_url": "https://evil.example/api/webhooks/1/2"}}, "invalid_discord_webhook"),
        ("telegram", {"config": {"chat_id": "abc"}}, "invalid_chat_id"),
        ("email", {"config": {"host": "smtp", "from_address": "pbo@x.org", "to_addresses": "nope"}}, "invalid_email"),
        ("webhook", {"config": {"url": "https://h/x", "header_names": ["Bad Header"]}}, "invalid_header"),
        ("apprise", {"secrets": {"url": "notaservice://x"}}, "invalid_apprise_url"),
    ],
)
def test_invalid_channels_are_refused_with_a_code(db, secret_key, kind, change, code):
    base = {"name": "x", "kind": kind, "config": {}, "secrets": {}}
    good = {
        "ntfy": {"config": {"topic": "ok"}},
        "gotify": {"config": {"server_url": "https://g"}, "secrets": {"app_token": "t"}},
        "discord": {},
        "telegram": {"secrets": {"bot_token": "123456:ABCdefGHIjklMNOpqrSTUvwxYZ"}},
        "email": {},
        "webhook": {},
        "apprise": {},
    }[kind]
    payload = {**base, **good}
    for key, value in change.items():
        payload[key] = {**payload.get(key, {}), **value}
    with pytest.raises(ChannelError) as raised:
        apply_payload(NotificationChannel(), payload, creating=True)
    assert raised.value.code == code


def test_secrets_are_encrypted_and_never_returned(client, db, session_factory, secret_key):
    client.post("/api/v1/setup/admin", json={"username": "admin", "password": "correct-horse-battery"})
    created = client.post(
        "/api/v1/notifications/channels",
        json={"name": "Phone", "kind": "ntfy", "config": {"topic": "pbo"}, "secrets": {"token": "tk_SUPERSECRET"}, "events": ["run_failed"]},
    )
    assert created.status_code == 201, created.text
    assert "tk_SUPERSECRET" not in created.text and created.json()["secrets_set"] == ["token"]
    with session_factory() as session:
        row = session.get(NotificationChannel, created.json()["id"])
        assert "tk_SUPERSECRET" not in (row.secrets_encrypted or "") and read_secrets(row) == {"token": "tk_SUPERSECRET"}
    listed = client.get("/api/v1/notifications/channels")
    assert "tk_SUPERSECRET" not in listed.text
    # Absent = keep, null = clear, string = replace.
    channel_id = created.json()["id"]
    kept = client.put(f"/api/v1/notifications/channels/{channel_id}", json={"name": "Phone", "config": {"topic": "pbo"}, "events": ["run_failed"]})
    assert kept.json()["secrets_set"] == ["token"]
    cleared = client.put(f"/api/v1/notifications/channels/{channel_id}", json={"name": "Phone", "config": {"topic": "pbo"}, "secrets": {"token": None}})
    assert cleared.json()["secrets_set"] == []
    refused = client.post("/api/v1/notifications/channels", json={"name": "x", "kind": "telegram", "config": {"chat_id": "1"}, "secrets": {}})
    assert refused.status_code == 422 and refused.json()["detail"] == {"code": "required", "field": "bot_token"}


def test_failed_send_error_is_readable_without_the_secret(db, sent):
    channel = _channel(db, "ntfy")
    sent.outcome.update(ok=False, error="Sending to ntfys://ntfy.example.org?token=tk_0123456789 failed: 401 Unauthorized")

    result = engine.send_test(db, channel)
    assert result["ok"] is False and "401 Unauthorized" in result["error"] and "tk_0123456789" not in result["error"]
    [row] = db.query(NotificationDelivery).all()
    assert (row.status, row.event) == ("failed", "test")


# --- engine ----------------------------------------------------------------------------------


def _disk(db, **values):
    disk = ExternalDisk(serial_number=values.pop("serial_number", "WD-1"), display_name=values.pop("display_name", "Rotation A"), color=values.pop("color", "blue"), **values)
    db.add(disk)
    db.commit()
    return disk


def test_emit_filters_by_event_and_level_with_disk_name_colour_and_link(db, sent):
    disk = _disk(db)
    _channel(db, "ntfy", events=["run_failed"], min_level="warning")
    assert engine.emit("disk_new", disk=disk, db=db) == 0  # event not chosen
    _channel(db, "gotify", events=["disk_new"], min_level="warning")
    assert engine.emit("disk_new", disk=disk, db=db) == 0  # info < warning
    assert engine.emit("run_failed", disk=disk, params={"detail": "PBS refused"}, db=db, dedup="run:1") == 1
    [delivery] = db.query(NotificationDelivery).all()
    assert delivery.title == "Backup failed" and delivery.body == "🔵 Rotation A: PBS refused" and delivery.link == f"https://pbo.lan/disks/{disk.id}"
    assert engine.process_queue(db, NOW) == 1
    assert sent.calls[0][3] == "failure"


def test_french_messages_follow_the_admin_language(db, sent):
    from app.models import User

    db.add(User(username="admin", password_hash="x", language="fr"))
    disk = _disk(db, color="green", display_name="Rotation B")
    _channel(db, "ntfy", events=["unsafe_removal"])
    engine.emit("unsafe_removal", disk=disk, db=db)
    delivery = db.query(NotificationDelivery).one()
    assert delivery.title == "Disque retiré sans éjection" and delivery.body.startswith("🟢 Rotation B :")


def test_identical_alerts_are_folded_not_sent_ten_times(db, sent):
    disk = _disk(db)
    _channel(db, "ntfy", events=["disk_space"])
    for _ in range(10):
        engine.emit("disk_space", disk=disk, level="warning", params={"percent": 80, "used": 800, "total": 1000}, dedup=f"{disk.id}:80", db=db)
    [delivery] = db.query(NotificationDelivery).all()
    assert delivery.duplicates == 9
    engine.process_queue(db, NOW)
    assert len(sent.calls) == 1
    # A new level is a new alert; the window is the event's (24 h for space).
    engine.emit("disk_space", disk=disk, level="error", params={"percent": 90}, dedup=f"{disk.id}:90", db=db)
    assert db.query(NotificationDelivery).count() == 2
    # Run events are never folded (their key is the run).
    _channel(db, "gotify", events=["run_failed"])
    engine.emit("run_failed", disk=disk, dedup="run:1", db=db)
    engine.emit("run_failed", disk=disk, dedup="run:2", db=db)
    assert db.query(NotificationDelivery).filter_by(event="run_failed").count() == 2


def test_quiet_hours_hold_everything_but_errors(db, sent, monkeypatch):
    monkeypatch.setattr(engine, "_now", lambda: datetime(2026, 11, 18, 22, 30, tzinfo=UTC))  # 23:30 in Paris
    disk = _disk(db)
    _channel(db, "ntfy", events=["disk_new", "run_failed"], quiet_start="22:00", quiet_end="07:30")
    engine.emit("disk_new", disk=disk, db=db)
    engine.emit("run_failed", disk=disk, dedup="run:9", db=db)
    held, urgent = db.query(NotificationDelivery).order_by(NotificationDelivery.id).all()
    assert engine._aware(held.next_attempt_at) == datetime(2026, 11, 19, 6, 30, tzinfo=UTC)  # 07:30 Paris
    assert engine.process_queue(db, datetime(2026, 11, 18, 22, 31, tzinfo=UTC)) == 1
    assert [call[1] for call in sent.calls] == ["Backup failed"]
    assert engine.process_queue(db, datetime(2026, 11, 19, 6, 31, tzinfo=UTC)) == 1
    assert held.status == urgent.status == "sent"


def test_quiet_window_inside_one_day():
    channel = NotificationChannel(quiet_start="12:00", quiet_end="14:00")
    zone = engine.ZoneInfo("UTC")
    assert engine.quiet_until(channel, datetime(2026, 1, 1, 13, 0, tzinfo=UTC), zone) == datetime(2026, 1, 1, 14, 0, tzinfo=UTC)
    assert engine.quiet_until(channel, datetime(2026, 1, 1, 15, 0, tzinfo=UTC), zone) is None


def test_daily_summary_groups_messages_once_a_day(db, sent, monkeypatch):
    monkeypatch.setattr(engine, "_now", lambda: datetime(2026, 11, 18, 5, 0, tzinfo=UTC))
    disk = _disk(db)
    _channel(db, "telegram", events=["disk_new", "unsafe_removal", "run_failed"], digest=True, digest_time="08:00")
    engine.emit("disk_new", disk=disk, db=db)
    engine.emit("unsafe_removal", disk=disk, db=db)
    engine.emit("run_failed", disk=disk, dedup="run:3", db=db)  # errors are not held
    morning = datetime(2026, 11, 18, 6, 0, tzinfo=UTC)  # 07:00 Paris: not yet
    engine.process_queue(db, morning)
    assert [call[1] for call in sent.calls] == ["Backup failed"]
    engine.process_queue(db, morning + timedelta(hours=2))
    assert sent.calls[-1][1] == "PBO daily summary (2)" and "New disk detected" in sent.calls[-1][2]
    engine.process_queue(db, morning + timedelta(hours=3))
    assert len(sent.calls) == 2  # once a day


def test_failures_are_retried_then_given_up_with_the_error(db, sent):
    _channel(db, "ntfy", events=["agent_offline"])
    sent.outcome.update(ok=False, error="Connection refused")
    engine.emit("agent_offline", params={"role": "host", "host": "pve"}, db=db)
    delivery = db.query(NotificationDelivery).one()
    moment = NOW
    for attempt in range(engine.MAX_ATTEMPTS):
        engine.process_queue(db, moment)
        moment = moment + timedelta(hours=2)
    assert (delivery.status, delivery.attempts, delivery.last_error) == ("failed", engine.MAX_ATTEMPTS, "Connection refused")
    assert len(sent.calls) == engine.MAX_ATTEMPTS


def test_emit_never_raises_and_is_off_without_a_database(db):
    assert engine.emit("disk_new") == 0  # no session factory (tests, before the lifespan)
    assert engine.emit("not_an_event", db=db) == 0


def test_catalog_has_both_languages_and_every_placeholder_is_known():
    for event in catalog.EVENTS:
        assert set(event.title) == set(event.body) == {"en", "fr"}
    title, body, link = catalog.message("backup_stale", "fr", {"disk": "🔵 A", "days": 31, "disk_id": 4})
    assert (title, body, link) == ("Aucune sauvegarde depuis 31 jours", "🔵 A : dernière sauvegarde il y a 31 jours.", "/disks/4")


# --- API -------------------------------------------------------------------------------------


def test_routes_test_a_draft_and_list_the_journal(client, sent, session_factory):
    client.post("/api/v1/setup/admin", json={"username": "admin", "password": "correct-horse-battery"})
    draft = client.post("/api/v1/notifications/test", json={"kind": "discord", "config": {}, "secrets": {"webhook_url": "https://discord.com/api/webhooks/1/abc"}})
    assert draft.json() == {"ok": True, "error": None} and sent.calls[0][0] == "discord://1/abc"
    created = client.post("/api/v1/notifications/channels", json={"name": "D", "kind": "discord", "secrets": {"webhook_url": "https://discord.com/api/webhooks/1/abc"}}).json()
    assert client.post(f"/api/v1/notifications/channels/{created['id']}/test").json()["ok"] is True
    journal = client.get("/api/v1/notifications/deliveries").json()
    assert [row["event"] for row in journal] == ["test"]
    catalog_body = client.get("/api/v1/notifications/catalog").json()
    assert {event["key"] for event in catalog_body["events"]} == set(catalog.EVENT_KEYS) and "webhook" in catalog_body["kinds"]
    assert client.delete(f"/api/v1/notifications/channels/{created['id']}").status_code == 204


# --- migration -------------------------------------------------------------------------------


def test_legacy_ntfy_settings_become_one_channel_once(db, secret_key):
    db.add_all(
        [
            AppSetting(key="NOTIFICATIONS_ENABLED", value="true"),
            AppSetting(key="NTFY_BASE_URL", value="https://ntfy.home.lan/"),
            AppSetting(key="NTFY_TOPIC", value="pbo-topic"),
            AppSetting(key="NTFY_USERNAME", value="pbo"),
            AppSetting(key="NTFY_PASSWORD", value=encrypt_value("ntfy-pass"), is_secret=True),
            AppSetting(key="NOTIFY_ON_BACKUP_SUCCESS", value="false"),
        ]
    )
    db.commit()
    channel = migrate_legacy_ntfy(db, {})
    assert channel is not None and channel.enabled and channel.kind == "ntfy"
    assert channel.config == {"server_url": "https://ntfy.home.lan", "topic": "pbo-topic", "username": "pbo"}
    assert read_secrets(channel) == {"password": "ntfy-pass"}
    assert "run_success" not in channel.events and {"run_failed", "disk_space", "agent_offline"} <= set(channel.events)
    assert migrate_legacy_ntfy(db, {}) is None and db.query(NotificationChannel).count() == 1  # once
    assert db.get(AppSetting, MARKER_KEY) is not None


def test_legacy_preferences_override_and_env_is_read_when_never_imported(db, secret_key):
    db.add(NotificationPreferences(id=1, notifications_enabled_override=False, notify_on_disk_new_detected=False))
    db.commit()
    channel = migrate_legacy_ntfy(db, {"NTFY_TOPIC": "from-env", "NOTIFICATIONS_ENABLED": "true"})
    assert channel.config["topic"] == "from-env" and channel.config["server_url"] == "https://ntfy.sh"
    assert channel.enabled is False and "disk_new" not in channel.events


def test_nothing_to_migrate_still_sets_the_marker(db, secret_key):
    assert migrate_legacy_ntfy(db, {}) is None
    assert db.get(AppSetting, MARKER_KEY) is not None and db.query(NotificationChannel).count() == 0


# --- retention -------------------------------------------------------------------------------


def _run(db, disk, days_ago, status=BackupRunStatus.SUCCESS, **extra):
    started = (NOW - timedelta(days=days_ago)).replace(tzinfo=None)
    run = ExternalBackupRun(
        disk_id=disk.id, status=status, started_at=started, finished_at=started, target_path="/x", datastore_name="d",
        mode=ExternalBackupMode.DEDICATED, created_at=started, stdout_log="log", **extra,
    )
    db.add(run)
    db.commit()
    return run


def test_retention_preview_then_delete_protects_what_explains_the_disk(db, secret_key):
    a = _disk(db, serial_number="A")
    b = _disk(db, serial_number="B", display_name="B")
    old_a = [_run(db, a, days) for days in (400, 300, 200)]
    recent_a = _run(db, a, 1)
    running = _run(db, a, 500, status=BackupRunStatus.RUNNING)
    only_b = _run(db, b, 900, status=BackupRunStatus.FAILED)  # the latest (only) run of B
    policy = run_retention.Policy(mode="days", days=180, log_detail_days=0)
    preview = run_retention.plan(db, policy, NOW)
    assert sorted(preview.runs) == sorted(run.id for run in old_a)
    assert recent_a.id not in preview.runs and running.id not in preview.runs and only_b.id not in preview.runs
    # A disk with an open alert keeps all its runs.
    a.verify_recommended = True
    db.commit()
    assert run_retention.plan(db, policy, NOW).runs == []
    a.verify_recommended = None
    db.commit()
    result = run_retention.apply(db, policy, NOW)
    assert len(result.runs) == 3 and db.query(ExternalBackupRun).count() == 3


def test_retention_per_disk_and_detailed_journals(db, secret_key):
    disk = _disk(db)
    runs = [_run(db, disk, days) for days in (50, 40, 30, 20, 10)]
    per_disk = run_retention.plan(db, run_retention.Policy(mode="per_disk", keep_per_disk=2), NOW)
    assert sorted(per_disk.runs) == sorted(run.id for run in runs[:3])
    logs = run_retention.apply(db, run_retention.Policy(mode="keep_all", log_detail_days=25), NOW)
    assert logs.runs == [] and sorted(logs.logs) == sorted(run.id for run in runs[:3])
    db.expire_all()
    assert db.get(ExternalBackupRun, runs[0].id).stdout_log is None and db.get(ExternalBackupRun, runs[0].id).status == BackupRunStatus.SUCCESS
    assert db.get(ExternalBackupRun, runs[-1].id).stdout_log == "log"


def test_retention_preview_route_and_settings_validation(client):
    client.post("/api/v1/setup/admin", json={"username": "admin", "password": "correct-horse-battery"})
    preview = client.post("/api/v1/settings/retention/preview", json={"mode": "days", "days": 30})
    assert preview.status_code == 200 and preview.json() == {"runs": 0, "logs": 0, "protected": 0}
    assert client.post("/api/v1/settings/retention/preview", json={"mode": "forever"}).status_code == 422
    assert client.patch("/api/v1/settings", json={"values": {"RUN_HISTORY_DAYS": 45}}).status_code == 422
    assert client.patch("/api/v1/settings", json={"values": {"PUBLIC_URL": "pbo.lan"}}).status_code == 422
    ok = client.patch("/api/v1/settings", json={"values": {"PUBLIC_URL": "https://pbo.lan", "UI_ACCENT": "teal", "RUN_HISTORY_MODE": "per_disk"}})
    assert ok.status_code == 200
    assert client.patch("/api/v1/settings", json={"values": {"UI_ACCENT": "pink"}}).status_code == 422


def test_about_compares_release_versions():
    from app.services.about import latest_release, update_available

    assert update_available("v0.3.0", "v0.4.1") is True
    assert update_available("v0.4.1", "v0.4.1") is False
    assert update_available("main@1a2b3c", "v0.4.1") is None
    assert latest_release(refresh=True, fetch=lambda: {"tag_name": "v1.2.3", "html_url": "https://x"})["version"] == "v1.2.3"
    assert latest_release(refresh=True, fetch=lambda: (_ for _ in ()).throw(OSError("offline"))) is None


def test_json_payload_of_the_generic_webhook_is_documented():
    """docs/NOTIFICATIONS.md documents Apprise's JSON payload plus PBO's extra keys."""
    from app.services.notification_channels import apprise_url as build

    channel = NotificationChannel(kind="webhook", config={"url": "http://hooks.lan/x", "header_names": []})
    url = build(channel, {}, extras={"event": "run_failed", "level": "error", "link": ""})
    plugin = apprise.Apprise.instantiate(url)
    assert plugin.payload_extras == {"event": "run_failed", "level": "error", "link": ""}
    assert json.dumps(sorted(plugin.payload_extras))


def test_daily_tasks_run_once_a_day_after_0330_with_retention_and_stale_reminders(db, sent, monkeypatch, session_factory):
    from app.services import daily_tasks

    monkeypatch.setattr(engine, "_session_factory", session_factory)  # what the lifespan sets
    monkeypatch.setattr(daily_tasks, "get_settings", lambda: Settings(timezone="Europe/Paris", run_history_mode="days", run_history_days=30))
    monkeypatch.setattr(run_retention, "get_settings", lambda: Settings(run_history_mode="days", run_history_days=30))
    disk = _disk(db, trusted=True, dedicated_backup_disk=True, prepared_as_pbs_datastore=True, connected=True)
    old = [_run(db, disk, days) for days in (90, 60)]
    _run(db, disk, 40)  # the latest run and backup of the disk: kept
    _channel(db, "ntfy", events=["backup_stale"])
    night = datetime(2026, 11, 18, 1, 0, tzinfo=UTC)  # 02:00 in Paris
    assert daily_tasks.due(db, night) is False
    morning = datetime(2026, 11, 18, 3, 0, tzinfo=UTC)  # 04:00 in Paris
    assert daily_tasks.due(db, morning) is True
    result = daily_tasks.run(db, morning)
    assert result["runs_deleted"] == len(old)
    assert result["stale_reminders"] == 1 and db.query(NotificationDelivery).filter_by(event="backup_stale").count() == 1
    assert daily_tasks.due(db, morning + timedelta(hours=5)) is False  # once a day


def test_apprise_log_prefix_is_dropped_from_the_error(monkeypatch):
    class Capture:
        def __init__(self, **_):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def getvalue(self):
            return "2026-11-18 18:50:37,743 - WARNING - A Connection error occurred sending ntfy:https://ntfy.example.org notification.\n"

    class Sender:
        def add(self, url):
            return True

        def notify(self, **_):
            return False

    monkeypatch.setattr(apprise, "Apprise", Sender)
    monkeypatch.setattr(apprise, "LogCapture", Capture)
    assert engine._apprise_notify("ntfys://x/y", "t", "b", "info") == (False, "A Connection error occurred sending ntfy:https://ntfy.example.org notification.")
