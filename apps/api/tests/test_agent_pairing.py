"""One-line agent install: pairing codes, registration, tokens, pinning."""

import io
import tarfile
from datetime import datetime, timedelta, timezone

import pytest

from app.core import config
from app.models.registered_agent import AgentPairingCode, RegisteredAgent
from app.services import agent_registry
from app.services.agent_bootstrap import AppUrlError, app_url_from_base, build_agent_archive, render_install_script
from app.services.host_agent import get_host_agent_client
from app.services.settings_service import activate_settings_store, effective_settings, update_settings
from tests.test_tls_pinning import WRONG, tls_server  # noqa: F401  (fixture)

ADMIN = {"username": "admin", "password": "correct horse battery"}
AGENT_FP = "AB:" * 31 + "CD"


@pytest.fixture(autouse=True)
def _reset_failures():
    agent_registry.reset_register_failures()
    yield
    agent_registry.reset_register_failures()


def _login(client):
    assert client.post("/api/v1/setup/admin", json=ADMIN).status_code == 201


def _pair(client) -> dict:
    response = client.post("/api/v1/agents/pairing")
    assert response.status_code == 201, response.text
    return response.json()


def _register(client, code, **overrides):
    payload = {
        "code": code,
        "role": "host",
        "hostname": "pve1",
        "base_url": "https://192.168.1.10:8090",
        "fingerprint": AGENT_FP.lower().replace(":", ""),
        **overrides,
    }
    return client.post("/api/v1/agents/register", json=payload)


def test_pairing_requires_a_session(client):
    assert client.post("/api/v1/agents/pairing").status_code == 401
    assert client.get("/api/v1/agents").status_code == 401


def test_pairing_code_and_install_command(client, db):
    _login(client)
    pairing = _pair(client)
    code = pairing["code"]
    assert len(code) == 9 and code[4] == "-"
    assert set(code.replace("-", "")) <= set(agent_registry.PAIRING_ALPHABET)
    assert pairing["install_command"] == (
        f'bash -c "$(curl -fskSL https://testserver/agent/install.sh)" -- --code {code}'
    )
    assert pairing["app_url"] == "https://testserver"
    expires = datetime.fromisoformat(pairing["expires_at"].replace("Z", "+00:00"))
    assert timedelta(minutes=14) < expires - datetime.now(timezone.utc) <= timedelta(minutes=15)
    # Only a hash is stored.
    stored = db.query(AgentPairingCode).one()
    assert code.replace("-", "") not in stored.code_hash


def test_register_returns_two_distinct_tokens_and_consumes_the_code(client, db, secret_key):
    _login(client)
    code = _pair(client)["code"]
    response = _register(client, code.lower())  # case and dash are forgiven
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["role"] == "host"
    assert body["agent_token"] != body["callback_token"]
    assert body["api_base_url"] == "https://testserver/api/v1"

    agent = db.query(RegisteredAgent).one()
    assert agent.fingerprint == AGENT_FP
    assert body["agent_token"] not in agent.token_encrypted
    assert agent.token_encrypted.startswith("fernet:")
    assert body["callback_token"] not in agent.callback_token_hash

    # Single use.
    again = _register(client, code)
    assert again.status_code == 401


def test_expired_and_unknown_codes_are_refused(client, db, secret_key):
    pairing = agent_registry.create_pairing_code(db, now=datetime.now(timezone.utc) - timedelta(minutes=16))
    assert _register(client, pairing.code).status_code == 401
    assert _register(client, "ABCD-EFGH").status_code == 401


def test_registration_input_is_validated(client, db, secret_key):
    code = agent_registry.create_pairing_code(db).code
    assert _register(client, code, role="admin").status_code == 422
    assert _register(client, code, base_url="http://192.168.1.10:8090").status_code == 422
    assert _register(client, code, base_url="https://0.0.0.0:8090").status_code == 422
    assert _register(client, code, base_url="https://pve1").status_code == 422
    assert _register(client, code, fingerprint="not-a-fingerprint").status_code == 422
    assert _register(client, code, hostname="bad host;rm").status_code == 422
    # Validation errors do not burn the code.
    assert _register(client, code).status_code == 201


def test_bruteforce_is_throttled(client, db, secret_key):
    for _ in range(agent_registry.REGISTER_MAX_FAILURES):
        assert _register(client, "ABCD-EFGH").status_code == 401
    code = agent_registry.create_pairing_code(db).code
    assert _register(client, code).status_code == 429


def test_new_registration_replaces_the_previous_agent_of_that_role(client, db, secret_key):
    first = _register(client, agent_registry.create_pairing_code(db).code).json()
    second = _register(client, agent_registry.create_pairing_code(db).code, hostname="pve2").json()
    pbs = _register(client, agent_registry.create_pairing_code(db).code, role="pbs").json()
    db.expire_all()
    agents = {agent.id: agent for agent in db.query(RegisteredAgent)}
    assert agents[first["agent_id"]].revoked_at is not None
    assert agents[second["agent_id"]].revoked_at is None
    assert agents[pbs["agent_id"]].revoked_at is None
    # The old callback token no longer authenticates.
    heartbeat = {"hostname": "pve1", "agent_version": "x", "observed_at": "2026-11-15T10:00:00Z"}
    old = client.post("/api/v1/agent/heartbeat", json=heartbeat, headers={"X-Agent-Token": first["callback_token"]})
    assert old.status_code == 401


def test_callback_token_authenticates_reports_but_not_the_agent_token(client, db, secret_key):
    body = _register(client, agent_registry.create_pairing_code(db).code).json()
    heartbeat = {"hostname": "pve1", "agent_version": "1.0", "observed_at": "2026-11-15T10:00:00Z"}
    ok = client.post("/api/v1/agent/heartbeat", json=heartbeat, headers={"X-Agent-Token": body["callback_token"]})
    assert ok.status_code == 200, ok.text
    db.expire_all()
    assert db.query(RegisteredAgent).one().last_seen_at is not None
    # The app -> agent token is not a callback credential.
    wrong_way = client.post("/api/v1/agent/heartbeat", json=heartbeat, headers={"X-Agent-Token": body["agent_token"]})
    assert wrong_way.status_code == 401


def test_run_log_callback_only_accepts_the_pbs_agent(client, db, secret_key):
    host = _register(client, agent_registry.create_pairing_code(db).code).json()
    _register(client, agent_registry.create_pairing_code(db).code, role="pbs")
    response = client.post(
        "/api/v1/external-backups/runs/1/log",
        json={"step": "x", "message": "y"},
        headers={"X-Agent-Token": host["callback_token"]},
    )
    assert response.status_code == 401


def test_static_legacy_tokens_still_accepted(client, db, secret_key):
    update_settings(db, {"HOST_AGENT_TOKEN": "legacy-host-token"})
    try:
        activate_settings_store(lambda: _SessionWrapper(db))
        heartbeat = {"hostname": "pve1", "agent_version": "1.0", "observed_at": "2026-11-15T10:00:00Z"}
        response = client.post(
            "/api/v1/agent/heartbeat", json=heartbeat, headers={"X-Agent-Token": "legacy-host-token"}
        )
        assert response.status_code == 200, response.text
    finally:
        config.set_settings_overlay_loader(None)


class _SessionWrapper:
    def __init__(self, db):
        self.db = db

    def __enter__(self):
        return self.db

    def __exit__(self, *exc):
        return False


def test_registered_agent_overrides_static_settings_and_is_pinned(client, db, secret_key):
    update_settings(db, {"HOST_AGENT_BASE_URL": "http://old:8090", "HOST_AGENT_TOKEN": "legacy"})
    body = _register(client, agent_registry.create_pairing_code(db).code).json()
    settings = effective_settings(db)
    assert settings.host_agent_base_url == "https://192.168.1.10:8090"
    assert settings.host_agent_token == body["agent_token"]
    assert settings.host_agent_fingerprint == AGENT_FP
    # PBS side untouched (no PBS agent registered).
    assert settings.pbs_agent_fingerprint == ""

    try:
        activate_settings_store(lambda: _SessionWrapper(db))
        host_client = get_host_agent_client()
        assert host_client.fingerprint == AGENT_FP
        assert host_client.token == body["agent_token"]
        # Revoking falls back to the static settings.
        assert client.delete(f"/api/v1/agents/{body['agent_id']}").status_code == 401  # no session
        agent_registry.revoke_agent(db, body["agent_id"])
        assert get_host_agent_client().base_url == "http://old:8090"
        assert get_host_agent_client().fingerprint == ""
    finally:
        config.set_settings_overlay_loader(None)


def test_registered_pbs_agent_gets_no_secret_in_callback_payload(db, secret_key):
    from app.services.external_backup_agent import ExternalBackupAgentBridge

    bridge = ExternalBackupAgentBridge.__new__(ExternalBackupAgentBridge)
    bridge.settings = config.Settings(pbs_agent_token="t", pbs_agent_fingerprint=AGENT_FP)
    assert bridge._callback_payload(7) == {"callback_run_id": 7}
    bridge.settings = config.Settings(pbs_agent_token="t")
    assert bridge._callback_payload(7)["callback_token"] == "t"


def test_agent_client_pins_the_registered_fingerprint(tls_server):  # noqa: F811
    from app.services.host_agent import HostAgentClient, HostAgentError

    base = tls_server["url"].replace("/api2/json", "")
    good = HostAgentClient(base_url=base, token="tok", timeout_seconds=5, label="Host agent", fingerprint=tls_server["fingerprint"])
    assert good.get_version() == {"data": {"version": "4.1.0"}}
    bad = HostAgentClient(base_url=base, token="tok", timeout_seconds=5, label="Host agent", fingerprint=WRONG)
    before = len(tls_server["seen"])
    with pytest.raises(HostAgentError):
        bad.get_version()
    assert len(tls_server["seen"]) == before  # nothing (no token) reached the server


def test_list_and_revoke_agents(client, db, secret_key):
    _login(client)
    body = _register(client, _pair(client)["code"]).json()
    listing = client.get("/api/v1/agents").json()
    assert listing[0]["id"] == body["agent_id"] and listing[0]["active"] is True
    assert "token" not in str(listing).lower().replace("fingerprint", "")
    revoked = client.delete(f"/api/v1/agents/{body['agent_id']}").json()
    assert revoked["active"] is False
    assert client.delete("/api/v1/agents/999").status_code == 404


def test_install_script_is_served_with_the_app_url(client):
    response = client.get("/agent/install.sh")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/x-shellscript")
    assert 'PBO_APP_URL="https://testserver"' in response.text
    assert "__PBO_APP_URL__" not in response.text
    assert "pveversion" in response.text


def test_install_script_refuses_plain_http(session_factory, secret_key):
    from fastapi.testclient import TestClient

    from app.db.session import get_db
    from app.main import app

    def _get_db():
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = _get_db
    try:
        response = TestClient(app, base_url="http://testserver").get("/agent/install.sh")
    finally:
        app.dependency_overrides.pop(get_db, None)
    assert response.status_code == 400


@pytest.mark.parametrize(
    "base, expected",
    [
        ("https://10.0.0.5/", "https://10.0.0.5"),
        ("https://10.0.0.5:443/", "https://10.0.0.5"),
        ("https://pbo.lan:8443/", "https://pbo.lan:8443"),
        ("https://[fd00::5]/", "https://[fd00::5]"),
    ],
)
def test_app_url_from_base(base, expected):
    assert app_url_from_base(base) == expected


@pytest.mark.parametrize("base", ["http://10.0.0.5/", "https://a$(reboot)/", "https://a b/", 'https://a"b/'])
def test_app_url_rejects_unsafe_values(base):
    with pytest.raises(AppUrlError):
        app_url_from_base(base)
    with pytest.raises(AppUrlError):
        render_install_script(base.rstrip("/"))


def test_agent_archive_contains_the_agent_package(client):
    response = client.get("/agent/agent.tar.gz")
    assert response.status_code == 200
    with tarfile.open(fileobj=io.BytesIO(response.content), mode="r:gz") as archive:
        names = archive.getnames()
    assert "pbo-agent/pyproject.toml" in names
    assert "pbo-agent/src/agent/server.py" in names
    assert not [name for name in names if "__pycache__" in name or name.endswith(".pyc") or "/tests" in name]
    build_agent_archive.cache_clear()
