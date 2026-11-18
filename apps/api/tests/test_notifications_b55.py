"""B55: messages in PBO's language (every event, both languages), the queue
placed again when quiet hours change, the filtered journal, the inbox."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from test_notifications_engine import _channel, _disk, sent  # noqa: F401 — fixture

from app.models import InboxItem, NotificationDelivery, User
from app.services import inbox, notifications
from app.services import notification_catalog as catalog
from app.services import notification_engine as engine

# English words that must never appear in a French message (B55: "Backup done",
# "was unplugged during the run"…). Proper nouns (Proxmox, PBS, SMART) are fine.
ENGLISH = re.compile(r"\b(the|was|is|during|failed|backup|done|unplugged|agent offline|disk|copy|run|warning|critical|host)\b", re.IGNORECASE)
FRENCH_ONLY = re.compile(r"\b(le|la|les|du|des|est|été|échec|sauvegarde|disque|pendant)\b", re.IGNORECASE)


def _run(**values):
    base = dict(id=7, status="failed", interrupted=None, repair=False, verify_only=False, current_step="failure",
                stdout_log="", message="PBS task failed: connection reset", started_at=None, finished_at=None)
    base.update(values)
    return SimpleNamespace(**base)


def _every_hook(disk):
    """Each hook once (several variants for runs), as PBO's code calls them."""
    started = datetime(2026, 11, 18, 10, 0)
    notifications.run_finished(_run(status="success", started_at=started, finished_at=started + timedelta(minutes=12)), disk)
    notifications.run_finished(_run(current_step="failure", stdout_log="[2026-11-18 10:00:00] sync_job PBS sync task started"), disk)
    notifications.run_finished(_run(id=8, current_step="handoff_usb_not_found"), disk)
    notifications.run_finished(_run(id=9, interrupted="during_sync", current_step="disk_pulled"), disk)
    notifications.run_finished(_run(id=10, current_step="cancelled"), SimpleNamespace(**vars(disk), pbs_handoff_slot=None))
    notifications.run_finished(_run(id=11, verify_only=True, current_step="verify_failed"), disk)
    notifications.run_finished(_run(id=12, status="success", repair=True), disk)
    notifications.unsafe_removal(disk)
    notifications.disk_to_plug(disk, "Weekly rotation", "19:37")
    notifications.rotation_missed(None, "Weekly rotation", disk_label="🟢 Rotation B", occurrence="3")
    notifications.backup_stale(disk, 12)
    notifications.disk_space(disk, 91, 455, 500)
    notifications.smart_degraded(disk, "danger", "SMART overall-health self-assessment test result: FAILED!")
    notifications.disk_new(disk)
    notifications.agent_offline("host", "pve1")
    notifications.agent_update_failed("host", "pve1", "rolled_back")
    notifications.integration_unreachable("pve", "https://10.0.0.2:8006")
    notifications.integration_unreachable("pbs", "https://pbs.lan:8007")
    notifications.guest_agent_missing()


@pytest.fixture
def inbox_db(db, monkeypatch, sent):  # noqa: F811
    monkeypatch.setattr(engine, "_session_factory", lambda: _NoClose(db))
    monkeypatch.setattr(inbox, "_publish", lambda: None)
    return db


class _NoClose:
    def __init__(self, db):
        self.db = db

    def __enter__(self):
        return self.db

    def __exit__(self, *exc):
        return False


@pytest.mark.parametrize("language", ["en", "fr"])
def test_every_event_reads_in_one_language(inbox_db, language):
    db = inbox_db
    db.add(User(username="admin", password_hash="x", language=language))
    db.commit()
    disk = _disk(db)
    _every_hook(disk)
    items = db.query(InboxItem).all()
    assert {item.event for item in items} == set(catalog.EVENT_KEYS)  # every event is covered
    for item in items:
        title, body, _link = catalog.message(item.event, language, item.params or {})
        text = f"{title} — {body}"
        assert "{" not in text and "  " not in text, text
        assert not re.search(r"\b(None|null)\b", text), text
        if language == "fr":
            assert not ENGLISH.search(text.replace("Proxmox Backup Server", "")), text  # a product name
        else:
            assert not FRENCH_ONLY.search(text), text
    # Free text from PBS / the agent never reaches a message.
    assert not any("connection reset" in (catalog.message(i.event, language, i.params or {})[1]) for i in items)
    assert not any("self-assessment" in (catalog.message(i.event, language, i.params or {})[1]) for i in items)


def test_catalog_has_both_languages_and_matching_placeholders():
    fields = re.compile(r"\{(\w+)\}")
    for event in catalog.EVENTS:
        for part in (event.title, event.body):
            assert set(part) == {"en", "fr"} and part["en"] != part["fr"] or event.key in {"pve_unreachable", "pbs_unreachable"} and part is event.title
            assert set(fields.findall(part["en"])) == set(fields.findall(part["fr"])), event.key
    for terms in (catalog.RUN_KINDS, catalog.STOP_REASONS, catalog.FAILED_STAGES, catalog.SMART_STATES, catalog.AGENT_ROLES, catalog.UPDATE_RESULTS):
        assert all(set(value) == {"en", "fr"} for value in terms.values())


def test_a_message_follows_the_language_of_the_moment_it_is_sent_and_shown(inbox_db, sent):  # noqa: F811
    db = inbox_db
    admin = User(username="admin", password_hash="x", language="en")
    db.add(admin)
    db.commit()
    disk = _disk(db)
    _channel(db, "ntfy", events=["run_interrupted"], quiet_start="00:00", quiet_end="23:59")
    notifications.run_finished(_run(interrupted="during_sync"), disk)
    delivery = db.query(NotificationDelivery).one()
    assert delivery.title == "Backup interrupted" and delivery.status == "pending"
    admin.language = "fr"  # changed in Settings before the message leaves
    db.commit()
    assert engine.delivery_view(delivery, engine.admin_language(db))["title"] == "Sauvegarde interrompue"
    engine.process_queue(db, datetime(2026, 11, 19, 23, 59, 30, tzinfo=UTC))
    assert sent.calls[-1][1] == "Sauvegarde interrompue" and "débranché pendant la copie" in sent.calls[-1][2]


# --- quiet hours changed: the queue is placed again ------------------------------------------------


def test_removing_quiet_hours_sends_what_waits_at_once(db, sent, monkeypatch):  # noqa: F811
    now = datetime(2026, 11, 18, 22, 30, tzinfo=UTC)  # 23:30 Paris
    monkeypatch.setattr(engine, "_now", lambda: now)
    disk = _disk(db)
    channel = _channel(db, "ntfy", events=["disk_new"], quiet_start="22:00", quiet_end="07:30")
    engine.emit("disk_new", disk=disk, db=db)
    held = db.query(NotificationDelivery).one()
    assert engine._aware(held.next_attempt_at) == datetime(2026, 11, 19, 6, 30, tzinfo=UTC)
    channel.quiet_start = channel.quiet_end = None
    assert engine.reschedule_channel(db, channel) == 1
    assert engine._aware(held.next_attempt_at) == now
    assert engine.process_queue(db, now) == 1 and held.status == "sent"


def test_new_quiet_hours_end_earlier_and_a_summary_holds_then_releases(db, sent, monkeypatch):  # noqa: F811
    now = datetime(2026, 11, 18, 22, 30, tzinfo=UTC)
    monkeypatch.setattr(engine, "_now", lambda: now)
    disk = _disk(db)
    channel = _channel(db, "ntfy", events=["disk_new", "run_failed"], quiet_start="22:00", quiet_end="07:30")
    engine.emit("disk_new", disk=disk, db=db)
    held = db.query(NotificationDelivery).one()
    channel.quiet_end = "23:45"
    engine.reschedule_channel(db, channel)
    assert engine._aware(held.next_attempt_at) == datetime(2026, 11, 18, 22, 45, tzinfo=UTC)
    channel.digest = True
    engine.reschedule_channel(db, channel)
    assert (held.status, held.next_attempt_at) == ("digest", None)
    channel.digest = False
    channel.quiet_start = channel.quiet_end = None
    engine.reschedule_channel(db, channel)
    assert held.status == "pending" and engine._aware(held.next_attempt_at) == now


def test_a_retry_keeps_its_delay(db, sent, monkeypatch):  # noqa: F811
    monkeypatch.setattr(engine, "_now", lambda: datetime(2026, 11, 18, 12, 0, tzinfo=UTC))
    channel = _channel(db, "ntfy", events=["agent_offline"])
    sent.outcome.update(ok=False, error="Connection refused")
    engine.emit("agent_offline", params={"role": "host", "host": "pve"}, db=db)
    engine.process_queue(db, datetime(2026, 11, 18, 12, 0, tzinfo=UTC))
    delivery = db.query(NotificationDelivery).one()
    before = delivery.next_attempt_at
    assert engine.reschedule_channel(db, channel) == 0 and delivery.next_attempt_at == before


def test_updating_a_channel_over_the_api_reschedules(client, session_factory, sent, monkeypatch):  # noqa: F811
    client.post("/api/v1/setup/admin", json={"username": "admin", "password": "correct-horse-battery"})
    monkeypatch.setattr(engine, "_now", lambda: datetime(2026, 11, 18, 22, 30, tzinfo=UTC))
    created = client.post(
        "/api/v1/notifications/channels",
        json={"name": "n", "kind": "ntfy", "config": {"topic": "t"}, "events": ["disk_new"], "quiet_start": "22:00", "quiet_end": "07:30"},
    ).json()
    with session_factory() as db:
        engine.emit("disk_new", params={"disk": "X"}, db=db)
    body = {**created, "quiet_start": None, "quiet_end": None}
    assert client.put(f"/api/v1/notifications/channels/{created['id']}", json=body).status_code == 200
    with session_factory() as db:
        delivery = db.query(NotificationDelivery).one()
        assert engine._aware(delivery.next_attempt_at) == datetime(2026, 11, 18, 22, 30, tzinfo=UTC)


# --- journal: filters, search, pages --------------------------------------------------------------


def test_the_journal_is_filtered_searched_and_paged(client, session_factory, sent):  # noqa: F811
    client.post("/api/v1/setup/admin", json={"username": "admin", "password": "correct-horse-battery"})
    with session_factory() as db:
        a = _channel(db, "ntfy", name="Phone", events=list(catalog.EVENT_KEYS))
        _channel(db, "discord", name="Team", events=["agent_offline"])
        for index in range(30):
            engine.emit("disk_new", params={"disk": f"Disk {index}"}, dedup=str(index), db=db)
        engine.emit("agent_offline", params={"role": "host", "host": "pve"}, db=db)
        failed = db.query(NotificationDelivery).filter_by(event="agent_offline", channel_id=a.id).one()
        failed.status, failed.last_error = "failed", "Connection refused"
        db.commit()
        a_id = a.id
    page = client.get("/api/v1/notifications/deliveries?limit=10").json()
    assert page["total"] == 32 and len(page["items"]) == 10 and page["items"][0]["event"] == "agent_offline"
    second = client.get("/api/v1/notifications/deliveries?limit=10&offset=30").json()
    assert len(second["items"]) == 2
    assert client.get(f"/api/v1/notifications/deliveries?channel_id={a_id}").json()["total"] == 31
    assert client.get("/api/v1/notifications/deliveries?status=failed").json()["total"] == 1
    assert client.get("/api/v1/notifications/deliveries?level=error").json()["total"] == 2
    assert client.get("/api/v1/notifications/deliveries?q=refused").json()["total"] == 1
    assert client.get("/api/v1/notifications/deliveries?q=Disk%2017").json()["total"] == 1
    assert client.get("/api/v1/notifications/deliveries?since=2100-01-01T00:00:00Z").json()["total"] == 0


# --- inbox --------------------------------------------------------------------------------------------


def test_the_inbox_gets_every_event_without_any_channel(inbox_db):
    db = inbox_db
    disk = _disk(db)
    notifications.disk_new(disk)
    notifications.disk_new(disk)  # same alert in its window: one line, counted twice
    notifications.run_finished(_run(), disk)
    assert db.query(NotificationDelivery).count() == 0
    listing = inbox.list_items(db, "fr")
    assert [item["event"] for item in listing["items"]] == ["run_failed", "disk_new"]
    assert listing["items"][1]["count"] == 2 and listing["items"][1]["title"] == "Nouveau disque détecté"
    assert listing["items"][0]["link"] == f"/disks/{disk.id}"
    assert (listing["unread"], listing["urgent_unread"]) == (2, 1)


def test_inbox_routes_filters_mark_read_and_toasts(client, session_factory, monkeypatch):
    client.post("/api/v1/setup/admin", json={"username": "admin", "password": "correct-horse-battery"})
    monkeypatch.setattr(inbox, "_publish", lambda: None)
    with session_factory() as db:
        engine.emit("disk_new", params={"disk": "A"}, dedup="a", db=db)
        engine.emit("agent_offline", params={"role": "host", "host": "pve"}, db=db)
    toast = {"level": "error", "title": "Disk not plugged in", "body": "Plug it in.", "link": "/disks/1"}
    assert client.post("/api/v1/inbox/toasts", json=toast).status_code == 201
    assert client.post("/api/v1/inbox/toasts", json={**toast, "link": "https://evil.example"}).status_code == 201  # folded
    body = client.get("/api/v1/inbox?filter=urgent").json()
    assert [item["event"] for item in body["items"]] == ["toast", "agent_offline"] and body["items"][0]["count"] == 2
    assert body["unread"] == 3 and body["urgent_unread"] == 2
    first = client.get("/api/v1/inbox").json()["items"][-1]
    assert client.post("/api/v1/inbox/read", json={"ids": [first["id"]]}).json()["unread"] == 2
    assert [i["read"] for i in client.get("/api/v1/inbox?filter=unread").json()["items"]] == [False, False]
    assert client.post("/api/v1/inbox/read", json={}).json() == {"unread": 0, "urgent_unread": 0}
    assert client.post("/api/v1/inbox/toasts", json={"level": "info", "title": "x"}).status_code == 422  # alerts only


def test_inbox_retention_follows_the_run_history_setting(db, monkeypatch):
    now = datetime(2026, 11, 18, tzinfo=UTC)
    for days in (5, 40, 400):
        moment = now - timedelta(days=days)
        db.add(InboxItem(source="event", event="disk_new", level="info", dedup_key=str(days), created_at=moment, updated_at=moment))
    db.commit()
    settings = SimpleNamespace(run_history_mode="keep_all", run_history_days=30)
    monkeypatch.setattr(inbox, "get_settings", lambda: settings)
    assert inbox.purge(db, now) == 0
    settings.run_history_mode = "per_disk"
    assert inbox.purge(db, now) == 1  # a year
    settings.run_history_mode = "days"
    assert inbox.purge(db, now) == 1 and db.query(InboxItem).count() == 1


# --- PBO's icon, priority, "Open" (B55) ---------------------------------------------------------------


def test_ntfy_discord_telegram_get_pbo_icon_priority_and_open_action(db, sent):  # noqa: F811
    import apprise

    from app.services.notification_channels import apprise_url, read_secrets

    icon, link = "https://pbo.lan/favicon/web-app-manifest-192x192.png", "https://pbo.lan/disks/3"
    ntfy = _channel(db, "ntfy")
    url = apprise_url(ntfy, read_secrets(ntfy), level="error", link=link, icon=icon, open_label="Ouvrir")
    sender = apprise.Apprise.instantiate(url)
    assert sender.avatar_url == icon and sender.include_image and sender.priority == "max"
    assert sender.click == link and sender._NotifyNtfy__actions == f"view, Ouvrir, {link}, clear=true"
    assert apprise.Apprise.instantiate(apprise_url(ntfy, read_secrets(ntfy), level="info")).include_image is False  # never Apprise's icon
    discord = _channel(db, "discord")
    sender = apprise.Apprise.instantiate(apprise_url(discord, read_secrets(discord), link=link, icon=icon))
    assert (sender.avatar_url, sender.href, sender.user) == (icon, link, "PBO")
    telegram = _channel(db, "telegram")
    assert apprise.Apprise.instantiate(apprise_url(telegram, read_secrets(telegram))).include_image is False
    # The engine passes PBO's icon (PUBLIC_URL) and the "Open" label of PBO's language.
    db.add(User(username="admin", password_hash="x", language="fr"))
    db.commit()
    engine.send_test(db, ntfy)
    assert "avatar_url=https%3A%2F%2Fpbo.lan%2Ffavicon%2Fweb-app-manifest-192x192.png" in sent.calls[-1][0]
    assert "Ouvrir" in __import__("urllib.parse").parse.unquote(sent.calls[-1][0])


# --- planning names (B55) -------------------------------------------------------------------------


def test_old_generated_planning_names_become_a_sentence_once(db):
    from app.models import AppSetting, ScheduledBackupEvent
    from app.models.scheduled_backup import ScheduledBackupRecurrenceType as Kind
    from app.models.scheduled_backup import ScheduledBackupStartMode
    from app.services.planning_titles import MARKER_KEY, rename_legacy_titles

    def event(title, kind, config, start):
        row = ScheduledBackupEvent(title=title, disk_serial="WD-1", datastore="main", recurrence_type=kind, recurrence_config=config,
                                   timezone="Europe/Paris", window_starts_at=start, start_mode=ScheduledBackupStartMode.AUTO_ON_DISK_DETECTED,
                                   created_at=start, updated_at=start)
        db.add(row)
        return row

    custom = event("Rotation tous les 1 · mercredi 19:37", Kind.WEEKLY, {"interval": 1, "weekdays": [2]}, datetime(2026, 11, 18, 18, 37))
    biweekly = event("Rotation every 2 weeks · Monday and Thursday 09:00", Kind.WEEKLY, {"interval": 2, "weekdays": [0, 3]}, datetime(2026, 11, 16, 8, 0))
    monthly = event("Rotation mensuelle · 5 · 03:00", Kind.MONTHLY, {"interval": 1}, datetime(2026, 12, 5, 2, 0))
    mine = event("Coffre du bureau · mardi", Kind.WEEKLY, {"interval": 1}, datetime(2026, 11, 17, 8, 0))
    db.commit()
    assert rename_legacy_titles(db) == 3
    assert custom.title == "Rotation chaque mercredi à 19:37"
    assert biweekly.title == "Rotation every 2 weeks on Monday and Thursday at 09:00"
    assert monthly.title == "Rotation le 5 de chaque mois à 03:00"
    assert mine.title == "Coffre du bureau · mardi"  # typed by the operator
    custom.title = "Rotation tous les 1 · mercredi 19:37"
    assert rename_legacy_titles(db) == 0 and db.get(AppSetting, MARKER_KEY) is not None  # once


def test_journal_rows_from_before_b55_get_their_title_in_the_current_language(db):
    from app.models import NotificationDelivery as Row

    old = Row(channel_id=1, channel_name="ntfy", event="run_success", level="info", dedup_key="k", title="Backup done", body="🔵 A: copy finished in 3 min.", status="sent")
    stale = Row(channel_id=1, channel_name="ntfy", event="backup_stale", level="warning", dedup_key="k2", title="No backup for 9 days", body="x", status="sent")
    assert engine.render_delivery(old, "fr") == ("Sauvegarde terminée", "🔵 A: copy finished in 3 min.")
    assert engine.render_delivery(stale, "fr")[0] == "No backup for 9 days"  # its title needs a number it no longer has
