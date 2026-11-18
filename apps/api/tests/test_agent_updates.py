"""Phase 5g: agents updated to the build this app ships."""

import hashlib
import io
import tarfile
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.models import ActivityEvent, AgentUpdate, BackupRunStatus, ExternalBackupMode, ExternalBackupRun, RegisteredAgent
from app.services import agent_updates
from app.services.agent_bootstrap import agent_source_dir, build_agent_archive, shipped_agent
from app.services.host_agent import HostAgentError, HostAgentResult
from tests.test_agent_pairing import _login, _pair, _register

SHIPPED = shipped_agent()


def _agent(client):
    _login(client)
    body = _register(client, _pair(client)["code"]).json()
    return body


def _heartbeat(client, token, build_id=None):
    payload = {"hostname": "pve1", "agent_version": "0.1.0", "observed_at": "2026-11-18T10:00:00Z"}
    if build_id:
        payload["build_id"] = build_id
    return client.post("/api/v1/agent/heartbeat", json=payload, headers={"X-Agent-Token": token})


# --- package -------------------------------------------------------------------------------------


def test_the_served_archive_is_the_announced_one(client):
    archive = client.get("/agent/agent.tar.gz").content
    assert hashlib.sha256(archive).hexdigest() == SHIPPED["sha256"]
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        assert tar.extractfile("pbo-agent/BUILD_ID").read().decode().strip() == SHIPPED["build_id"]
    # Reproducible: same sources, same bytes.
    build_agent_archive.cache_clear()
    assert hashlib.sha256(build_agent_archive(str(agent_source_dir()))).hexdigest() == SHIPPED["sha256"]


# --- heartbeat -------------------------------------------------------------------------------------


def test_heartbeat_records_the_build_and_starts_an_update_when_older(client, db, _no_background_agent_update):
    body = _agent(client)
    assert _heartbeat(client, body["callback_token"], "0123456789abcdef").status_code == 200
    agent = db.get(RegisteredAgent, body["agent_id"])
    db.refresh(agent)
    assert (agent.agent_version, agent.build_id) == ("0.1.0", "0123456789abcdef")
    (update_id,) = _no_background_agent_update
    row = db.get(AgentUpdate, update_id)
    assert (row.trigger, row.status, row.from_build, row.to_build) == ("auto", "running", "0123456789abcdef", SHIPPED["build_id"])


def test_an_up_to_date_agent_or_the_option_off_starts_nothing(client, db, _no_background_agent_update):
    body = _agent(client)
    _heartbeat(client, body["callback_token"], SHIPPED["build_id"])
    with patch("app.services.agent_updates.get_settings", lambda: SimpleNamespace(auto_update_agents=False)):
        _heartbeat(client, body["callback_token"], "0123456789abcdef")
    assert _no_background_agent_update == []


# --- running an update -----------------------------------------------------------------------------


class FakeAgentClient:
    """`/version`, `/self-update`, `/self-update/status` of an agent."""

    def __init__(self, *, capabilities=("self-update",), build="0123456789abcdef", outcome="success", restart_errors=2):
        self.capabilities = list(capabilities)
        self.build = build
        self.outcome = outcome
        self.restart_errors = restart_errors
        self.calls = []

    def get_version(self):
        return {"agent_version": "0.1.0", "build_id": self.build, "capabilities": self.capabilities}

    def post(self, path, payload):
        self.calls.append((path, payload))
        return HostAgentResult(True, "", None, None, None, None, None, {"ok": True, "status": "started"})

    def get(self, path, params=None):
        self.calls.append((path, None))
        if self.restart_errors:
            self.restart_errors -= 1
            raise HostAgentError("restarting", stdout_log=None, stderr_log=None, command_summary=None, execution_cwd=None, return_code=None)
        if self.outcome == "silent":
            raise HostAgentError("down", stdout_log=None, stderr_log=None, command_summary=None, execution_cwd=None, return_code=None)
        new = SHIPPED["build_id"] if self.outcome == "success" else self.build
        state = {"status": self.outcome, "to_build": SHIPPED["build_id"], "message": f"agent says {self.outcome}"}
        return HostAgentResult(True, "", None, None, None, None, None, {"ok": True, "build_id": new, "update": state})


def _run(db, session_factory, client_, agent_id, trigger="manual"):
    agent = db.get(RegisteredAgent, agent_id)
    clock = iter(range(0, 10_000, 3))
    with patch("app.services.notifications.agent_update_failed") as notify:
        row = agent_updates.start_update(
            db,
            agent,
            trigger=trigger,
            wait=True,
            executor=lambda update_id: agent_updates.execute_update(
                update_id, session_factory=session_factory, client_factory=lambda a: client_, sleep=lambda s: None, clock=lambda: next(clock)
            ),
        )
    db.expire_all()
    return db.get(AgentUpdate, row.id), notify


def test_success_keeps_the_tokens_and_records_the_new_build(client, db, session_factory):
    body = _agent(client)
    fake = FakeAgentClient()
    row, notify = _run(db, session_factory, fake, body["agent_id"])
    assert (row.status, row.code) == ("success", None)
    assert fake.calls[0] == ("/self-update", {"build_id": SHIPPED["build_id"], "sha256": SHIPPED["sha256"]})
    agent = db.get(RegisteredAgent, body["agent_id"])
    assert agent.build_id == SHIPPED["build_id"] and agent.revoked_at is None
    assert _heartbeat(client, body["callback_token"], SHIPPED["build_id"]).status_code == 200  # same tokens
    notify.assert_not_called()  # Phase 5j: only a failed or cancelled update is notified


def test_rollback_is_an_alert(client, db, session_factory):
    body = _agent(client)
    row, notify = _run(db, session_factory, FakeAgentClient(outcome="rolled_back"), body["agent_id"])
    assert (row.status, row.code) == ("rolled_back", "health_check_failed")
    event = db.query(ActivityEvent).filter_by(category="agent").one()
    assert (event.action, event.status) == ("agent_update_rolled_back", "error")
    assert notify.call_args.args[:2] == ("host", "pve") or notify.called
    # Not retried automatically for this build ("Update now" still can).
    agent = db.get(RegisteredAgent, body["agent_id"])
    assert agent_updates.maybe_auto_update(db, agent) is None
    assert agent_updates.agent_state(db, agent, SHIPPED) == "failed"


def test_an_agent_that_never_answers_again_is_failed(client, db, session_factory):
    body = _agent(client)
    row, _ = _run(db, session_factory, FakeAgentClient(outcome="silent"), body["agent_id"])
    assert (row.status, row.code) == ("failed", "agent_unreachable_after_update")


def test_an_agent_too_old_to_update_itself(client, db, session_factory):
    body = _agent(client)
    fake = FakeAgentClient(capabilities=("pbs-guest-mounts",))
    row, _ = _run(db, session_factory, fake, body["agent_id"])
    assert (row.status, row.code) == ("too_old", "agent_too_old")
    assert fake.calls == []  # nothing pushed


def test_never_during_a_run(client, db):
    body = _agent(client)
    now = datetime.utcnow()
    db.add(ExternalBackupRun(disk_id=1, status=BackupRunStatus.RUNNING, started_at=now, created_at=now, target_path="/x", datastore_name="main", mode=ExternalBackupMode.DEDICATED))
    db.commit()
    response = client.post(f"/api/v1/agents/{body['agent_id']}/update")
    assert response.status_code == 409 and response.json()["detail"]["code"] == "run_active"
    assert db.query(AgentUpdate).one().status == "busy"


def test_update_now_and_overview(client, db, _no_background_agent_update):
    body = _agent(client)
    _heartbeat(client, body["callback_token"], SHIPPED["build_id"])
    response = client.post(f"/api/v1/agents/{body['agent_id']}/update")
    assert response.status_code == 202 and _no_background_agent_update == [response.json()["id"]]
    again = client.post(f"/api/v1/agents/{body['agent_id']}/update")
    assert again.status_code == 409 and again.json()["detail"]["code"] == "update_running"
    overview = client.get("/api/v1/agents/updates").json()
    assert overview["auto_update"] is True and overview["shipped"]["build_id"] == SHIPPED["build_id"]
    assert overview["agents"][0]["state"] == "updating"
    assert overview["history"][0]["trigger"] == "manual"


def test_the_update_command_updates_every_agent(client, db):
    body = _agent(client)
    ran = []
    with patch.object(agent_updates, "execute_update", lambda update_id: ran.append(update_id)):
        rows = agent_updates.update_all(db, trigger="update_command")
    assert [row.trigger for row in rows] == ["update_command"] and len(ran) == 1
    assert rows[0].agent_id == body["agent_id"]


@pytest.mark.parametrize("value", ["true", "false"])
def test_setting_is_registered(db, value):
    from app.services.settings_service import update_settings, effective_settings

    update_settings(db, {"AUTO_UPDATE_AGENTS": value})
    assert effective_settings(db).auto_update_agents is (value == "true")
