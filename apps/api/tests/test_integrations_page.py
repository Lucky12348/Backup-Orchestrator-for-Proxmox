"""Phase 6b: Integrations page — health cards, "Test", revoked agents purge."""

from datetime import datetime, timezone
from types import SimpleNamespace

from app.models.registered_agent import RegisteredAgent
from app.services import integration_health

ADMIN = {"username": "admin", "password": "correct horse battery"}


def _settings(**overrides):
    values = dict(
        pve_api_url="https://pve:8006/api2/json", pve_api_token_id="pbo@pve!pbo", pve_fingerprint="AA:BB", pve_node_name="pve",
        pbs_api_url="", pbs_token_id="", pbs_fingerprint="", pbs_datastore="main",
        host_agent_base_url="https://pve:8090", host_agent_fingerprint="CC:DD",
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def test_health_cards(monkeypatch):
    class _Pve:
        def __init__(self, settings):
            pass

        def _get(self, path):
            assert path == "version"
            return {"version": "9.0.3"}

    class _Agent:
        def get_version(self):
            raise RuntimeError("connection refused")

    monkeypatch.setattr("app.services.proxmox_client.ProxmoxClient", _Pve)
    monkeypatch.setattr("app.services.host_agent.get_host_agent_client", lambda: _Agent())
    pve = integration_health.check("pve", _settings())
    assert pve["ok"] is True and pve["version"] == "9.0.3" and pve["fingerprint"] == "AA:BB" and pve["latency_ms"] >= 0
    pbs = integration_health.check("pbs", _settings())
    assert pbs["ok"] is False and pbs["code"] == "not_configured"
    agent = integration_health.check("agent", _settings())
    assert agent["ok"] is False and agent["code"] == "unreachable" and "refused" in agent["message"]
    assert agent["fingerprint"] == "CC:DD"
    assert not any("token" in key or "secret" in key for key in pve)  # no secret in the answer


def test_health_routes(client, monkeypatch):
    assert client.get("/api/v1/integrations/health").status_code == 401
    assert client.post("/api/v1/setup/admin", json=ADMIN).status_code == 201
    monkeypatch.setattr(integration_health, "check_all", lambda: [{"key": "pve", "ok": True}])
    assert client.get("/api/v1/integrations/health").json() == [{"key": "pve", "ok": True}]
    monkeypatch.setattr(integration_health, "check", lambda key, settings=None: {"key": key, "ok": False})
    assert client.post("/api/v1/integrations/pbs/test").json() == {"key": "pbs", "ok": False}
    assert client.post("/api/v1/integrations/nope/test").status_code == 404


def test_delete_revoked_agents(client, db):
    assert client.post("/api/v1/setup/admin", json=ADMIN).status_code == 201
    now = datetime.now(timezone.utc)
    for index, revoked in enumerate([None, now, now]):
        db.add(RegisteredAgent(role="host", hostname=f"pve{index}", base_url="https://pve:8090", fingerprint="AA",
                               token_encrypted="x", callback_token_hash=f"{index:064d}", created_at=now, revoked_at=revoked))
    db.commit()
    response = client.delete("/api/v1/agents/revoked")
    assert response.status_code == 200 and response.json() == {"deleted": 2}
    agents = client.get("/api/v1/agents").json()
    assert [agent["hostname"] for agent in agents] == ["pve0"]


def test_login_lockout_is_explained(client):
    assert client.post("/api/v1/setup/admin", json=ADMIN).status_code == 201
    client.cookies.clear()
    lefts = []
    for _ in range(5):
        response = client.post("/api/v1/auth/token", data={"username": "admin", "password": "wrong"})
        assert response.status_code == 401
        lefts.append(response.json()["detail"]["attempts_left"])
    assert lefts == [4, 3, 2, 1, 0]
    locked = client.post("/api/v1/auth/token", data={"username": "admin", "password": "correct horse battery"})
    assert locked.status_code == 429
    assert locked.json()["detail"]["code"] == "login_locked"
    assert 0 < int(locked.headers["retry-after"]) <= 301
