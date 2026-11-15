import httpx
import pytest

from app.models.app_setting import AppSetting
from app.services import setup_probe
from app.services.settings_service import effective_settings
from app.services.setup_probe import ProbeError, normalize_api_url, normalize_fingerprint, probe_pbs, probe_proxmox

FP_HEX = "ab" * 32
FP = normalize_fingerprint(FP_HEX)


def _pve_transport(seen_headers):
    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.append(request.headers.get("authorization"))
        path = request.url.path
        if path.endswith("/version"):
            return httpx.Response(200, json={"data": {"version": "8.3.2"}})
        if path.endswith("/nodes"):
            return httpx.Response(200, json={"data": [{"node": "pve2"}, {"node": "pve1"}]})
        if path.endswith("/nodes/pve1/qemu"):
            return httpx.Response(200, json={"data": [{"vmid": 101, "name": "pbs", "status": "running"}]})
        if path.endswith("/nodes/pve2/qemu"):
            return httpx.Response(200, json={"data": [{"vmid": 100, "name": "web", "status": "stopped"}]})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def _pbs_transport():
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/version"):
            return httpx.Response(200, json={"data": {"version": "3.4.1"}})
        if path.endswith("/nodes"):
            return httpx.Response(200, json={"data": [{"node": "localhost"}]})
        if path.endswith("/admin/datastore"):
            return httpx.Response(200, json={"data": [{"store": "main", "comment": "primary"}, {"store": "archive"}]})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def test_normalizers():
    assert normalize_api_url("pve.lan", 8006) == "https://pve.lan:8006/api2/json"
    assert normalize_api_url("https://10.0.0.5:9000/whatever", 8006) == "https://10.0.0.5:9000/api2/json"
    with pytest.raises(ProbeError):
        normalize_api_url("http://pve.lan", 8006)
    assert normalize_fingerprint(FP_HEX.upper()) == FP
    assert normalize_fingerprint(None) == ""
    with pytest.raises(ProbeError):
        normalize_fingerprint("zz:11")


# Note: httpx.MockTransport bypasses TLS entirely, so these tests cover the API
# logic; the handshake-level pinning is tested against a real TLS server in
# tests/test_tls_pinning.py.


def test_probe_proxmox_with_pin_lists_nodes_vms():
    headers = []
    result = probe_proxmox(
        "pve.lan",
        "root@pam!pbo",
        "uuid-secret",
        verify_ssl=False,
        fingerprint=FP_HEX,
        transport=_pve_transport(headers),
    )
    assert result.version == "8.3.2"
    assert result.nodes == ["pve1", "pve2"]
    assert [vm["vmid"] for vm in result.vms] == [100, 101]
    assert result.fingerprint == FP and result.fingerprint_pinned is True
    assert result.trust_required is False
    assert headers[0] == "PVEAPIToken=root@pam!pbo=uuid-secret"


def test_unpinned_self_signed_probe_never_sends_the_token():
    headers = []
    result = probe_proxmox("pve.lan", "root@pam!pbo", "uuid-secret", verify_ssl=False, transport=_pve_transport(headers))
    assert result.trust_required is True
    assert result.nodes == [] and result.vms == []
    assert headers == [None]  # one unauthenticated request only


def test_probe_pbs_lists_datastores_and_reports_auth_errors():
    result = probe_pbs("pbs.lan", "pbo@pbs!probe", "s", verify_ssl=False, fingerprint=FP, transport=_pbs_transport())
    assert result.version == "3.4.1"
    assert result.fingerprint_pinned is True
    assert [store["name"] for store in result.datastores] == ["archive", "main"]

    denied = httpx.MockTransport(lambda request: httpx.Response(401))
    with pytest.raises(ProbeError, match="rejected the API token"):
        probe_pbs("pbs.lan", "a", "b", verify_ssl=False, fingerprint=FP, transport=denied)


@pytest.fixture
def admin_client(client):
    assert client.post("/api/v1/setup/admin", json={"username": "admin", "password": "correct horse battery"}).status_code == 201
    return client


def test_setup_test_endpoint_uses_stored_secret_when_omitted(admin_client, monkeypatch):
    calls = []

    def fake_probe(api_url, token_id, token_secret, **kwargs):
        calls.append(token_secret)
        return setup_probe.ProbeResult(api_url="https://pbs:8007/api2/json", version="3.4", fingerprint=FP, fingerprint_pinned=False)

    monkeypatch.setattr("app.api.routes.setup.probe_pbs", fake_probe)
    body = {"api_url": "pbs", "token_id": "pbo@pbs!t"}
    assert admin_client.post("/api/v1/setup/pbs/test", json=body).status_code == 400  # nothing stored yet

    assert admin_client.put("/api/v1/setup/pbs", json={**body, "token_secret": "stored-secret", "datastore": "main", "fingerprint": FP_HEX}).status_code == 200
    response = admin_client.post("/api/v1/setup/pbs/test", json=body)
    assert response.status_code == 200
    assert calls == ["stored-secret"]
    assert "stored-secret" not in response.text


def test_setup_test_endpoint_reports_probe_errors(admin_client, monkeypatch):
    def failing(*args, **kwargs):
        raise ProbeError("Proxmox VE API unreachable: boom")

    monkeypatch.setattr("app.api.routes.setup.probe_proxmox", failing)
    response = admin_client.post("/api/v1/setup/proxmox/test", json={"api_url": "pve", "token_id": "t", "token_secret": "s"})
    assert response.status_code == 400
    assert "unreachable" in response.json()["detail"]


def test_save_proxmox_persists_encrypted_settings(admin_client, session_factory):
    payload = {
        "api_url": "pve.lan",
        "token_id": "root@pam!pbo",
        "token_secret": "pve-secret-value",
        "verify_ssl": False,
        "fingerprint": FP_HEX,
        "node_name": "pve1",
        "pbs_vm_id": 101,
    }
    response = admin_client.put("/api/v1/setup/proxmox", json=payload)
    assert response.status_code == 200
    assert response.json()["proxmox_configured"] is True

    with session_factory() as db:
        settings = effective_settings(db)
        assert settings.pve_api_url == "https://pve.lan:8006/api2/json"
        assert settings.pve_api_token_secret == "pve-secret-value"
        assert settings.pve_fingerprint == FP
        assert settings.pbs_execution_vm_id == 101
        assert settings.pbs_execution_vm_node == "pve1"
        assert "pve-secret-value" not in db.get(AppSetting, "PVE_API_TOKEN_SECRET").value

    # Updating without re-typing the secret keeps it.
    payload.pop("token_secret")
    assert admin_client.put("/api/v1/setup/proxmox", json={**payload, "pbs_vm_id": 102}).status_code == 200
    with session_factory() as db:
        assert effective_settings(db).pve_api_token_secret == "pve-secret-value"
        assert effective_settings(db).pbs_execution_vm_id == 102
