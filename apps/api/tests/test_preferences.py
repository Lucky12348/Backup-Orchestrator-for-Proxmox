"""Per-account language, app time zone (wizard, planning), PBS address detection."""

import pytest
from sqlalchemy import create_engine, inspect

from app.core.timezones import InvalidTimezoneError, validate_timezone
from app.db.migrate import run_migrations
from app.models.scheduled_backup import ScheduledBackupEvent
from app.models.user import User
from app.services import stale_mounts  # noqa: F401
from app.services import setup_wizard
from app.services.host_agent import HostAgentError, HostAgentResult
from app.services.settings_service import (
    SettingValidationError,
    effective_settings,
    update_settings,
)

ADMIN = {"username": "admin", "password": "correct horse battery"}


# --- Language --------------------------------------------------------------------


def test_admin_creation_stores_language_and_time_zone(client, db):
    response = client.post("/api/v1/setup/admin", json={**ADMIN, "language": "fr", "timezone": "America/Montreal"})
    assert response.status_code == 201, response.text
    assert response.json()["language"] == "fr"
    assert db.query(User).one().language == "fr"
    assert effective_settings(db).timezone == "America/Montreal"
    assert client.get("/api/v1/auth/me").json()["language"] == "fr"


def test_language_is_per_account_and_returned_at_login(client, db):
    assert client.post("/api/v1/setup/admin", json=ADMIN).status_code == 201
    assert client.get("/api/v1/auth/me").json()["language"] is None  # not chosen yet
    assert client.put("/api/v1/auth/me/preferences", json={"language": "fr"}).json()["language"] == "fr"
    assert client.put("/api/v1/auth/me/preferences", json={"language": "de"}).status_code == 422
    client.post("/api/v1/auth/logout")
    assert client.put("/api/v1/auth/me/preferences", json={"language": "en"}).status_code == 401
    login = client.post("/api/v1/auth/token", data={"username": "admin", "password": ADMIN["password"]})
    assert login.status_code == 200 and login.json()["language"] == "fr"


def test_invalid_time_zone_is_refused_with_a_code(client, db):
    response = client.post("/api/v1/setup/admin", json={**ADMIN, "timezone": "Mars/Olympus"})
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_timezone"
    assert db.query(User).count() == 0  # nothing created


def test_users_language_migration(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'm.sqlite'}")
    run_migrations(engine)
    assert "language" in {column["name"] for column in inspect(engine).get_columns("users")}
    engine.dispose()


# --- Time zone ---------------------------------------------------------------------


def test_validate_timezone():
    assert validate_timezone(" Europe/Paris ") == "Europe/Paris"
    for bad in ("", "Nowhere/Land", "../etc/passwd", "/etc/localtime", "x" * 80):
        with pytest.raises(InvalidTimezoneError):
            validate_timezone(bad)


def test_timezone_setting_is_validated(db):
    update_settings(db, {"TIMEZONE": "Asia/Tokyo"})
    assert effective_settings(db).timezone == "Asia/Tokyo"
    with pytest.raises(SettingValidationError):
        update_settings(db, {"TIMEZONE": "Not/AZone"})


def _event_payload(**overrides):
    return {
        "title": "Weekly",
        "disk_serial": "SER1",
        "datastore": "main",
        "window_starts_at": "2026-12-01T01:00:00",
        **overrides,
    }


def test_new_plannings_use_the_app_time_zone_existing_ones_keep_theirs(client, db):
    assert client.post("/api/v1/setup/admin", json={**ADMIN, "timezone": "America/New_York"}).status_code == 201
    created = client.post("/api/v1/planning/events", json=_event_payload())
    assert created.status_code == 200, created.text
    assert created.json()["timezone"] == "America/New_York"

    explicit = client.post("/api/v1/planning/events", json=_event_payload(timezone="Europe/Paris")).json()
    assert explicit["timezone"] == "Europe/Paris"
    # Changing the app zone later does not touch existing plannings.
    update_settings(db, {"TIMEZONE": "Asia/Tokyo"})
    patched = client.patch(f"/api/v1/planning/events/{explicit['id']}", json={"title": "Renamed"})
    assert patched.status_code == 200 and patched.json()["timezone"] == "Europe/Paris"
    assert {event.timezone for event in db.query(ScheduledBackupEvent)} == {"America/New_York", "Europe/Paris"}
    assert client.post("/api/v1/planning/events", json=_event_payload(timezone="Bad/Zone")).status_code == 422


# --- PBS address detection -------------------------------------------------------------


class FakeAgent:
    def __init__(self, addresses=None, error=None, guest=None):
        self.addresses = addresses or []
        self.error = error
        self.guest = guest if guest is not None else {"enabled": True, "active": True}
        self.calls = []

    def post(self, path, payload):
        self.calls.append((path, payload))
        if self.error:
            raise self.error
        return HostAgentResult(
            ok=True, message="", stdout_log=None, stderr_log=None, command_summary=None,
            execution_cwd=None, return_code=0,
            payload={"ok": True, **self.guest} if path == "/pbs-guest/agent" else {"ok": True, "addresses": self.addresses},
        )


def test_detect_keeps_the_first_address_where_pbs_answers(db):
    agent = FakeAgent(
        [
            {"ip": "10.0.0.4", "mac": "", "source": "guest-agent"},
            {"ip": "10.0.0.5", "mac": "bc:24:11:aa:bb:cc", "source": "neighbour"},
        ]
    )
    result = setup_wizard.detect_pbs_url(db, 110, agent_client=agent, prober=lambda ip: ip == "10.0.0.5")
    assert result["detected"] is True
    assert result["url"] == "https://10.0.0.5:8007" and result["source"] == "neighbour"
    assert agent.calls == [("/pve/vm-addresses", {"vmid": 110}), ("/pbs-guest/agent", {"vmid": 110})]
    assert result["guest_agent"]["active"] is True


def test_detect_reports_a_missing_guest_agent_with_the_commands(db):
    """5f.5: no QEMU guest agent in the PBS VM = a non-blocking warning with the 3 commands."""
    agent = FakeAgent([{"ip": "10.0.0.5", "source": "neighbour"}], guest={"enabled": False, "active": False})
    result = setup_wizard.detect_pbs_url(db, 110, agent_client=agent, prober=lambda ip: True)
    assert result["detected"] is True
    assert result["guest_agent"] == {
        "enabled": False,
        "active": False,
        "commands": stale_mounts.guest_agent_commands(110),
    }


def test_detect_without_answer_leaves_the_field_empty(db):
    agent = FakeAgent([{"ip": "10.0.0.4", "source": "neighbour"}])
    result = setup_wizard.detect_pbs_url(db, 110, agent_client=agent, prober=lambda ip: False)
    assert result["detected"] is False and result["url"] is None
    assert setup_wizard.detect_pbs_url(db, 110, agent_client=FakeAgent([]), prober=lambda ip: True)["detected"] is False


def test_pbs_answers_on_401_only_over_https():
    import httpx

    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(401, json={"data": None})

    assert setup_wizard._pbs_answers("10.0.0.5", transport=httpx.MockTransport(handler)) is True
    assert seen == ["https://10.0.0.5:8007/api2/json/version"]
    assert setup_wizard._pbs_answers("10.0.0.5", transport=httpx.MockTransport(lambda r: httpx.Response(404))) is False


def test_detect_route(client, monkeypatch):
    assert client.post("/api/v1/setup/admin", json=ADMIN).status_code == 201
    agent = FakeAgent([{"ip": "10.0.0.5", "source": "neighbour"}])
    monkeypatch.setattr(setup_wizard, "host_agent_client", lambda db: agent)
    monkeypatch.setattr(setup_wizard, "_pbs_answers", lambda ip, transport=None: True)
    body = client.get("/api/v1/setup/pbs/detect", params={"vmid": 110}).json()
    assert body["detected"] is True and body["url"] == "https://10.0.0.5:8007"
    assert client.get("/api/v1/setup/pbs/detect", params={"vmid": 5}).status_code == 400

    error = HostAgentError("x", stdout_log=None, stderr_log=None, command_summary=None, execution_cwd=None,
                           return_code=404, status_code=404)
    monkeypatch.setattr(setup_wizard, "host_agent_client", lambda db: FakeAgent(error=error))
    response = client.get("/api/v1/setup/pbs/detect", params={"vmid": 110})
    assert response.status_code == 502 and response.json()["detail"]["code"] == "agent_outdated"


def test_notifications_step_is_accepted_for_later(client):
    """Prepared optional step: the server already stores it (the UI hides it)."""
    assert client.post("/api/v1/setup/admin", json=ADMIN).status_code == 201
    response = client.put("/api/v1/setup/progress", json={"step": "notifications"})
    assert response.status_code == 200 and response.json()["current_step"] == "notifications"
