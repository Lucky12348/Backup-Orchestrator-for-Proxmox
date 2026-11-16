"""Real-time channel: event bus, SSE stream (auth, heartbeat, Last-Event-ID),
disk events from agent reports, run.* events from the SQLAlchemy hooks."""

import asyncio
from datetime import datetime, timezone

import pytest

from app.models.activity_event import ActivityEvent
from app.models.backup_run import BackupRunStatus
from app.models.external_backup_run import ExternalBackupMode, ExternalBackupRun
from app.models.external_disk import ExternalDisk
from app.schemas.agent import AgentDiskReportCreate
from app.services import events, run_events
from app.services.disks import ingest_agent_disk_report

ADMIN = {"username": "admin", "password": "correct horse battery"}


@pytest.fixture(autouse=True)
def _fresh_bus():
    events.bus.reset()
    run_events.reset_agent_presence()
    yield
    events.bus.reset()
    run_events.reset_agent_presence()


def _types(since: int = 0):
    return [(event.type, event.data) for event in events.bus.since(since)]


# --- Bus ----------------------------------------------------------------------


def test_bus_numbers_events_and_replays_after_an_id():
    first = events.bus.publish("disk.added", {"id": 1})
    second = events.bus.publish("disk.removed", {"id": 1})
    assert (first.id, second.id) == (1, 2)
    assert [event.id for event in events.bus.since(1)] == [2]
    assert events.bus.since(None) == []
    assert events.bus.since(99) == []  # id from a previous process: no replay
    with pytest.raises(ValueError):
        events.bus.publish("disk.exploded", {})


def test_sse_format():
    event = events.bus.publish("run.log", {"run_id": 3, "lines": ["a"]})
    text = event.to_sse()
    assert text.startswith("id: 1\nevent: run.log\ndata: {")
    assert text.endswith("\n\n")


def _collect(stream, count):
    async def run():
        out = []
        async for chunk in stream:
            out.append(chunk)
            if len(out) >= count:
                break
        await stream.aclose()
        return out

    return asyncio.run(run())


def test_stream_replays_then_heartbeats():
    for index in range(3):
        events.bus.publish("disk.updated", {"id": index})

    async def connected():
        return False

    chunks = _collect(events.event_stream(events.bus, 1, is_disconnected=connected, heartbeat_seconds=0.01), 4)
    assert chunks[0].startswith("retry: 3000")
    assert chunks[1].startswith("id: 2\n") and chunks[2].startswith("id: 3\n")
    assert chunks[3] == ": ping\n\n"
    assert events.bus.subscriber_count == 0  # unsubscribed on close


def test_stream_delivers_live_events_once():
    async def scenario():
        async def connected():
            return False

        stream = events.event_stream(events.bus, None, is_disconnected=connected, heartbeat_seconds=5)
        assert (await stream.__anext__()).startswith("retry:")
        pending = asyncio.ensure_future(stream.__anext__())
        await asyncio.sleep(0.05)
        events.bus.publish("agent.online", {"hostname": "pve"})
        chunk = await asyncio.wait_for(pending, 2)
        await stream.aclose()
        return chunk

    chunk = asyncio.run(scenario())
    assert "event: agent.online" in chunk and '"hostname":"pve"' in chunk


def test_stream_stops_when_the_client_leaves():
    async def gone():
        return True

    chunks = _collect(events.event_stream(events.bus, None, is_disconnected=gone, heartbeat_seconds=5), 10)
    assert len(chunks) == 1


# --- Route --------------------------------------------------------------------


def test_events_route_requires_a_session(client):
    assert client.get("/api/v1/events").status_code == 401


def test_events_route_streams_with_last_event_id(client, monkeypatch):
    monkeypatch.setattr(events, "MAX_STREAM_SECONDS", 0.3)
    monkeypatch.setattr(events, "HEARTBEAT_SECONDS", 0.1)
    assert client.post("/api/v1/setup/admin", json=ADMIN).status_code == 201
    for index in range(3):
        events.bus.publish("disk.added", {"id": index})
    response = client.get("/api/v1/events", headers={"Last-Event-ID": "2"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"].startswith("no-cache")
    body = response.text
    assert "id: 3\n" in body and "id: 1\n" not in body and "id: 2\n" not in body
    assert ": ping" in body


# --- Disk events --------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_side_effects(monkeypatch):
    # ntfy notifications and planning hooks are out of scope here (and slow).
    for name in ("notify_new_disk_detected", "notify_known_disk_detected", "handle_disk_detected"):
        monkeypatch.setattr(f"app.services.disks.{name}", lambda *args, **kwargs: None)


def _report(db, disks, hotplug=None):
    payload = AgentDiskReportCreate(
        hostname="pve",
        observed_at=datetime.now(timezone.utc),
        disks=disks,
        trigger="udev",
        hotplug=hotplug or [],
    )
    return ingest_agent_disk_report(db, payload)


def _disk(serial, capacity_gb, **extra):
    return {
        "serial_number": serial,
        "display_name": f"Disk {serial}",
        "model_name": "Samsung T7",
        "capacity_gb": capacity_gb,
        "candidate_type": "usb",
        "connected": True,
        **extra,
    }


def test_report_publishes_added_rejected_updated_and_removed(db):
    _report(
        db,
        [_disk("GOOD1", 1000), _disk("EMPTY", 0), _disk("SMALL", 16)],
        hotplug=[{"action": "added", "serial": "GOOD1", "model": "Samsung T7"}],
    )
    added = {data["serial_number"]: data for kind, data in _types() if kind == "disk.added"}
    assert added["GOOD1"]["status"] == "new" and added["GOOD1"]["reject_reason"] is None
    assert added["GOOD1"]["capacity_gb"] == 1000 and added["GOOD1"]["model_name"] == "Samsung T7"
    assert added["EMPTY"]["status"] == "rejected" and added["EMPTY"]["reject_reason"] == "zero_size"
    assert added["SMALL"]["status"] == "rejected" and added["SMALL"]["reject_reason"] == "too_small"
    history = db.query(ActivityEvent).filter_by(category="disk").all()
    assert [(item.action, item.details["serial"]) for item in history] == [("usb_added", "GOOD1")]

    last = events.bus.since(0)[-1].id
    _report(db, [_disk("GOOD1", 1000, filesystem_type="ext4"), _disk("EMPTY", 0), _disk("SMALL", 16)])
    assert [(kind, data["serial_number"]) for kind, data in _types(last)] == [("disk.updated", "GOOD1")]

    last = events.bus.since(0)[-1].id
    _report(db, [_disk("EMPTY", 0), _disk("SMALL", 16)], hotplug=[{"action": "removed", "serial": "GOOD1"}])
    removed = _types(last)
    assert [(kind, data["serial_number"]) for kind, data in removed] == [("disk.removed", "GOOD1")]
    assert removed[0][1]["connected"] is False and removed[0][1]["status"] == "unplugged"

    last = events.bus.since(0)[-1].id
    _report(db, [_disk("EMPTY", 0), _disk("SMALL", 16)])  # nothing changed: no event
    assert _types(last) == []


def test_known_trusted_disk_comes_back_as_known(db):
    _report(db, [_disk("TRUST", 2000)])
    disk = db.query(ExternalDisk).filter_by(serial_number="TRUST").one()
    disk.trusted = True
    db.commit()
    _report(db, [])
    last = events.bus.since(0)[-1].id
    _report(db, [_disk("TRUST", 2000)])
    assert [(kind, data["status"]) for kind, data in _types(last)] == [("disk.added", "known")]


# --- Run events (SQLAlchemy hooks) ----------------------------------------------


def _run(db):
    disk = ExternalDisk(serial_number="RUN1", display_name="Run disk", capacity_gb=1000, source="agent")
    db.add(disk)
    db.commit()
    run = ExternalBackupRun(
        disk_id=disk.id,
        status=BackupRunStatus.RUNNING,
        started_at=datetime.utcnow(),
        created_at=datetime.utcnow(),
        target_path="/mnt/datastore/x",
        datastore_name="x",
        mode=ExternalBackupMode.DEDICATED,
        stdout_log="[t] start\n",
    )
    db.add(run)
    db.commit()
    return run


def test_run_progress_log_and_finished_are_published_after_commit(db):
    run = _run(db)
    created = _types()
    assert ("run.progress", "running") in [(kind, data.get("status")) for kind, data in created]

    last = events.bus.since(0)[-1].id
    run.progress_percent = 42.5
    run.current_step = "sync"
    run.downloaded_bytes = 12_000_000_000
    run.current_speed = "110 MiB/s"
    run.stdout_log += "[t] line one\n[t] line two\n"
    db.flush()
    assert _types(last) == []  # not before the commit
    db.commit()
    published = _types(last)
    progress = next(data for kind, data in published if kind == "run.progress")
    assert progress["progress_percent"] == 42.5 and progress["current_step"] == "sync"
    assert progress["downloaded_bytes"] == 12_000_000_000 and progress["current_speed"] == "110 MiB/s"
    assert progress["kind"] == "external_backup" and progress["run_id"] == run.id
    log = next(data for kind, data in published if kind == "run.log")
    assert log["lines"] == ["[t] line one", "[t] line two"] and log["stream"] == "stdout"

    last = events.bus.since(0)[-1].id
    run.status = BackupRunStatus.SUCCESS
    run.finished_at = datetime.utcnow()
    db.commit()
    assert "run.finished" in [kind for kind, _ in _types(last)]


def test_rolled_back_run_changes_are_not_published(db):
    run = _run(db)
    last = events.bus.since(0)[-1].id
    run.progress_percent = 99
    db.flush()
    db.rollback()
    assert _types(last) == []


def test_new_log_lines():
    assert run_events.new_log_lines("a\n", "a\nb\nc\n") == ["b", "c"]
    assert run_events.new_log_lines("a\n", "a\n") == []
    assert run_events.new_log_lines("old", "rewritten\n") == ["rewritten"]


# --- Agent presence -------------------------------------------------------------


def test_agent_presence_transitions_only():
    run_events.update_agent_presence(False)  # startup without agent: silent
    run_events.update_agent_presence(True, "pve")
    run_events.update_agent_presence(True, "pve")
    run_events.update_agent_presence(False, "pve")
    assert [kind for kind, _ in _types()] == ["agent.online", "agent.offline"]
