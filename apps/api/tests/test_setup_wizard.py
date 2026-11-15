"""First-run wizard: progress, PVE provisioning through the host agent, PBS
provisioning with the root@pam password (never stored nor logged)."""

import functools
import json
import logging
import re
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

from app.models.app_setting import AppSetting
from app.models.setup_progress import SetupProgress
from app.services import setup_wizard
from app.services.host_agent import HostAgentError, HostAgentResult
from app.services.pbs_provisioning import PBS_ACLS, provision_pbs_access
from app.services.settings_service import (
    effective_settings,
    stored_secret,
    update_settings,
)

ADMIN = {"username": "admin", "password": "correct horse battery"}
PVE_FP = "AA:" * 31 + "AA"
PBS_FP = "BB:" * 31 + "BB"
PVE_SECRET = "pve-secret-0000-1111"
PBS_SECRET = "pbs-secret-2222-3333"
ROOT_PASSWORD = "Sup3r-S3cret-R00t-Pa55"


@pytest.fixture(autouse=True)
def _reset_limiter():
    setup_wizard.pbs_provision_limiter.reset()
    yield
    setup_wizard.pbs_provision_limiter.reset()


def _login(client):
    assert client.post("/api/v1/setup/admin", json=ADMIN).status_code == 201


def _result(payload):
    return HostAgentResult(
        ok=True, message="ok", stdout_log=None, stderr_log=None, command_summary=None,
        execution_cwd=None, return_code=0, payload=payload,
    )


class FakeAgent:
    def __init__(self, provision=None, addresses=None, error=None):
        self.provision = provision or {
            "ok": True,
            "token_id": "pbo@pve!pbo",
            "secret": PVE_SECRET,
            "node": "pve",
            "pve_version": "8.2.4",
            "api_url": "https://192.168.1.10:8006",
            "fingerprint": PVE_FP.lower(),
        }
        self.addresses = addresses or {}
        self.error = error
        self.calls = []

    def post(self, path, payload):
        self.calls.append((path, payload))
        if self.error:
            raise self.error
        if path == "/pve/provision-access":
            return _result(self.provision)
        if path == "/pve/guest-addresses":
            return _result({"ok": True, "addresses": self.addresses})
        raise AssertionError(path)


def pve_transport(vms=None):
    vms = vms if vms is not None else [
        {"vmid": 100, "name": "web", "status": "running"},
        {"vmid": 110, "name": "backup-srv", "status": "running"},
        {"vmid": 120, "name": "pbs-old", "status": "stopped"},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == f"PVEAPIToken=pbo@pve!pbo={PVE_SECRET}"
        path = request.url.path
        if path.endswith("/version"):
            return httpx.Response(200, json={"data": {"version": "8.2.4"}})
        if path.endswith("/nodes"):
            return httpx.Response(200, json={"data": [{"node": "pve"}]})
        if path.endswith("/nodes/pve/qemu"):
            return httpx.Response(200, json={"data": vms})
        if path.endswith("/nodes/pve/lxc"):
            return httpx.Response(200, json={"data": [{"vmid": 200, "name": "ct", "status": "running"}]})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


@pytest.fixture
def pve_mocks(monkeypatch):
    agent = FakeAgent()
    monkeypatch.setattr(setup_wizard, "host_agent_client", lambda db: agent)
    original = setup_wizard.probe_proxmox
    transport = pve_transport()
    # provision_pve passes transport=None explicitly: force the fake one.
    monkeypatch.setattr(setup_wizard, "probe_proxmox", lambda *a, **k: original(*a, **{**k, "transport": transport}))
    return agent


# --- Progress / redirect state -------------------------------------------------


def test_progress_resumes_after_reload_and_strips_secrets(client):
    _login(client)
    assert client.get("/api/v1/setup/progress").json()["current_step"] == "agent"

    response = client.put(
        "/api/v1/setup/progress",
        json={"step": "pve", "data": {"note": "x", "token_secret": "leak", "nested": {"password": "p", "ok": 1}}},
    )
    assert response.status_code == 200
    again = client.get("/api/v1/setup/progress").json()  # "reload"
    assert again["current_step"] == "pve"
    assert again["data"] == {"note": "x", "nested": {"ok": 1}}
    status = client.get("/api/v1/setup/status").json()
    assert status["setup_completed"] is False and status["current_step"] == "pve"


def test_progress_requires_session_and_cannot_skip_to_done(client):
    assert client.get("/api/v1/setup/progress").status_code == 401
    _login(client)
    assert client.put("/api/v1/setup/progress", json={"step": "done"}).status_code == 400
    response = client.post("/api/v1/setup/complete")
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "pve_missing"


def test_upgraded_install_is_not_sent_to_the_wizard(client, db):
    from app.services.users import create_first_admin

    create_first_admin(db, "admin", ADMIN["password"])
    update_settings(
        db,
        {"PVE_API_URL": "https://10.0.0.1:8006/api2/json", "PVE_API_TOKEN_ID": "x@pve!y", "PVE_API_TOKEN_SECRET": "s"},
    )
    status = client.get("/api/v1/setup/status").json()
    assert status["setup_completed"] is True and status["current_step"] == "done"


def test_complete_marks_setup_done(client, db, pve_mocks):
    _login(client)
    assert client.post("/api/v1/setup/pve/provision").status_code == 200
    update_settings(
        db,
        {"PBS_API_URL": "https://10.0.0.5:8007/api2/json", "PBS_TOKEN_ID": "pbo@pbs!pbo", "PBS_TOKEN_SECRET": "s", "PBS_DATASTORE": "main"},
    )
    response = client.post("/api/v1/setup/complete")
    assert response.status_code == 200, response.text
    assert response.json()["completed"] is True
    assert client.get("/api/v1/setup/status").json()["setup_completed"] is True
    assert db.get(SetupProgress, 1).completed_at is not None


def test_agent_status_follows_the_first_heartbeat(client, db):
    from app.services.agent_registry import (
        authenticate_callback,
        create_pairing_code,
        register_agent,
    )

    _login(client)
    assert client.get("/api/v1/setup/agent").json() == {
        "registered": False, "connected": False, "agent_id": None, "hostname": None,
        "base_url": None, "fingerprint": None, "last_seen_at": None,
    }
    code = create_pairing_code(db).code
    registration = register_agent(
        db, code=code, role="host", hostname="pve", base_url="https://192.168.1.10:8090", fingerprint="AB" * 32
    )
    status = client.get("/api/v1/setup/agent").json()
    assert status["registered"] is True and status["connected"] is False
    authenticate_callback(db, registration.callback_token)  # heartbeat
    assert client.get("/api/v1/setup/agent").json()["connected"] is True


# --- Step 3: PVE --------------------------------------------------------------


def test_pve_provision_stores_pinned_token(client, db, pve_mocks):
    _login(client)
    response = client.post("/api/v1/setup/pve/provision")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["node"] == "pve" and body["pve_version"] == "8.2.4"
    assert body["vm_count"] == 3 and body["ct_count"] == 1
    assert PVE_SECRET not in response.text

    settings = effective_settings(db)
    assert settings.pve_api_url == "https://192.168.1.10:8006/api2/json"
    assert settings.pve_api_token_id == "pbo@pve!pbo"
    assert settings.pve_fingerprint == PVE_FP
    assert settings.pve_node_name == "pve"
    assert stored_secret(db, "PVE_API_TOKEN_SECRET") == PVE_SECRET
    assert PVE_SECRET not in db.get(AppSetting, "PVE_API_TOKEN_SECRET").value  # encrypted

    progress = client.get("/api/v1/setup/progress").json()
    assert progress["data"]["pve"]["node"] == "pve"
    assert PVE_SECRET not in json.dumps(progress)


def test_pve_provision_outdated_agent_is_explained(client, monkeypatch):
    error = HostAgentError("not served", stdout_log=None, stderr_log=None, command_summary=None,
                           execution_cwd=None, return_code=404, status_code=404)
    monkeypatch.setattr(setup_wizard, "host_agent_client", lambda db: FakeAgent(error=error))
    _login(client)
    response = client.post("/api/v1/setup/pve/provision")
    assert response.status_code == 502
    detail = response.json()["detail"]
    assert detail["code"] == "agent_outdated"
    assert "Re-run its install command" in detail["message"]


def test_pve_provision_without_agent(client):
    _login(client)
    response = client.post("/api/v1/setup/pve/provision")
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "agent_missing"


def test_pbs_candidates_prefers_port_8007(client, db, monkeypatch, pve_mocks):
    _login(client)
    assert client.post("/api/v1/setup/pve/provision").status_code == 200
    pve_mocks.addresses = {"100": ["10.0.0.4"], "110": ["10.0.0.5"]}
    original = setup_wizard.pbs_candidates
    monkeypatch.setattr(
        "app.api.routes.setup.setup_wizard.pbs_candidates",
        functools.partial(original, port_probe=lambda address: address == "10.0.0.5"),
    )
    body = client.get("/api/v1/setup/pbs/candidates").json()
    assert body["suggested_vmid"] == 110
    first = body["vms"][0]
    assert first["vmid"] == 110 and first["suggested_url"] == "https://10.0.0.5:8007" and first["pbs_port_open"]
    by_id = {vm["vmid"]: vm for vm in body["vms"]}
    assert by_id[120]["name_match"] is True and by_id[120]["score"] == 1
    # Only running VMs are asked to the guest agent.
    assert ("/pve/guest-addresses", {"vmids": [100, 110]}) in pve_mocks.calls


# --- Step 4: PBS --------------------------------------------------------------


class FakePbs:
    def __init__(self, *, version="3.4.1", user=False, token=False, password=ROOT_PASSWORD):
        self.version = version
        self.users = {"root@pam"} | ({"pbo@pbs"} if user else set())
        self.tokens = {"pbo@pbs!pbo"} if token else set()
        self.acls: set[tuple[str, str, str]] = set()
        self.password = password
        self.created_tokens = 0
        self.log: list[str] = []

    def transport(self):
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/api2/json/")
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()} if request.content else {}
        self.log.append(f"{request.method} {path}")
        auth = request.headers.get("Authorization", "")
        if path == "access/ticket":
            if form.get("username") == "root@pam" and form.get("password") == self.password:
                return httpx.Response(200, json={"data": {"ticket": "PBS:root@pam:ABC::sig", "CSRFPreventionToken": "csrf"}})
            return httpx.Response(401, json={"data": None, "message": "authentication failed - invalid credentials"})
        ticket_ok = "PBSAuthCookie=PBS:root@pam:ABC::sig" in request.headers.get("Cookie", "")
        token_ok = auth == f"PBSAPIToken=pbo@pbs!pbo:{PBS_SECRET}" and "pbo@pbs!pbo" in self.tokens
        if not (ticket_ok or token_ok):
            return httpx.Response(401, json={"data": None})
        if request.method != "GET" and ticket_ok and request.headers.get("CSRFPreventionToken") != "csrf":
            return httpx.Response(401, json={"data": None, "message": "invalid csrf"})
        if path == "version":
            return httpx.Response(200, json={"data": {"version": self.version, "release": "1"}})
        if path == "nodes":
            # PBS list_nodes has no `access` block: superuser only, 403 for any token.
            if token_ok:
                return httpx.Response(403, json={"data": None, "message": "permission check failed"})
            return httpx.Response(200, json={"data": [{"node": "localhost"}]})
        if path == "admin/datastore":
            return httpx.Response(200, json={"data": [{"store": "main"}, {"store": "offsite"}]})
        if path == "access/users" and request.method == "GET":
            return httpx.Response(200, json={"data": [{"userid": u} for u in sorted(self.users)]})
        if path == "access/users" and request.method == "POST":
            self.users.add(form["userid"])
            return httpx.Response(200, json={"data": None})
        if path == "access/users/pbo@pbs" and request.method == "PUT":
            return httpx.Response(200, json={"data": None})
        if path == "access/users/pbo@pbs/token" and request.method == "GET":
            return httpx.Response(200, json={"data": [{"tokenid": t} for t in sorted(self.tokens)]})
        if path == "access/users/pbo@pbs/token/pbo":
            if request.method == "DELETE":
                self.tokens.discard("pbo@pbs!pbo")
                return httpx.Response(200, json={"data": None})
            if request.method == "POST":
                if "pbo@pbs!pbo" in self.tokens:
                    return httpx.Response(400, json={"data": None, "message": "token already exists"})
                self.tokens.add("pbo@pbs!pbo")
                self.created_tokens += 1
                return httpx.Response(200, json={"data": {"tokenid": "pbo@pbs!pbo", "value": PBS_SECRET}})
        if path == "access/acl" and request.method == "PUT":
            self.acls.add((form["path"], form["role"], form["auth-id"]))
            return httpx.Response(200, json={"data": None})
        return httpx.Response(404, json={"data": None})


def _pbs_payload(**overrides):
    return {
        "api_url": "10.0.0.5",
        "fingerprint": PBS_FP,
        "fingerprint_confirmed": True,
        "password": ROOT_PASSWORD,
        "pbs_vm_id": 110,
        **overrides,
    }


@pytest.fixture
def fake_pbs(monkeypatch):
    pbs = FakePbs()
    monkeypatch.setattr(
        "app.api.routes.setup.provision_pbs_access", functools.partial(provision_pbs_access, transport=pbs.transport())
    )
    original_select = setup_wizard.select_pbs_datastore
    monkeypatch.setattr(
        "app.api.routes.setup.setup_wizard.select_pbs_datastore",
        functools.partial(original_select, transport=pbs.transport()),
    )
    return pbs


def _assert_password_absent(password, caplog, db, *texts):
    assert password not in caplog.text
    for text in texts:
        assert password not in text
    for row in db.query(AppSetting).all():
        assert password not in (row.value or "")
    for row in db.query(SetupProgress).all():
        assert password not in row.data


def test_pbs_provision_creates_user_token_and_acls(client, db, fake_pbs, caplog):
    caplog.set_level(logging.DEBUG)
    _login(client)
    response = client.post("/api/v1/setup/pbs/provision", json=_pbs_payload())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["version"] == "3.4.1" and body["token_id"] == "pbo@pbs!pbo"
    assert [store["name"] for store in body["datastores"]] == ["main", "offsite"]
    assert "pbo@pbs" in fake_pbs.users
    expected = {(path, role, auth) for path, role in PBS_ACLS for auth in ("pbo@pbs", "pbo@pbs!pbo")}
    assert fake_pbs.acls == expected
    assert PBS_SECRET not in response.text
    assert not any(line == "GET nodes" for line in fake_pbs.log)

    settings = effective_settings(db)
    assert settings.pbs_api_url == "https://10.0.0.5:8007/api2/json"
    assert settings.pbs_fingerprint == PBS_FP and settings.pbs_mode == "api"
    assert settings.pbs_execution_vm_id == 110
    assert stored_secret(db, "PBS_TOKEN_SECRET") == PBS_SECRET

    assert client.put("/api/v1/setup/pbs/datastore", json={"datastore": "nope"}).status_code == 400
    assert client.put("/api/v1/setup/pbs/datastore", json={"datastore": "main"}).status_code == 200
    assert effective_settings(db).pbs_datastore == "main"
    assert client.get("/api/v1/setup/progress").json()["data"]["pbs"]["datastore"] == "main"
    _assert_password_absent(ROOT_PASSWORD, caplog, db, response.text)


def test_pbs_provision_is_idempotent(client, db, monkeypatch, caplog):
    pbs = FakePbs(user=True, token=True)
    monkeypatch.setattr(
        "app.api.routes.setup.provision_pbs_access", functools.partial(provision_pbs_access, transport=pbs.transport())
    )
    _login(client)
    assert client.post("/api/v1/setup/pbs/provision", json=_pbs_payload()).status_code == 200
    assert "DELETE access/users/pbo@pbs/token/pbo" in pbs.log
    assert "POST access/users" not in pbs.log
    assert pbs.created_tokens == 1


def test_wrong_password_is_explained_and_never_logged(client, db, fake_pbs, caplog):
    caplog.set_level(logging.DEBUG)
    _login(client)
    wrong = "Wr0ng-Pa55w0rd-Value"
    response = client.post("/api/v1/setup/pbs/provision", json=_pbs_payload(password=wrong))
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["code"] == "bad_credentials"
    assert "password" in detail["message"]
    assert "pbo@pbs" not in fake_pbs.users
    _assert_password_absent(wrong, caplog, db, response.text)


def test_pbs_provision_rate_limited(client, fake_pbs):
    _login(client)
    for _ in range(5):
        assert client.post("/api/v1/setup/pbs/provision", json=_pbs_payload(password="bad")).status_code == 400
    response = client.post("/api/v1/setup/pbs/provision", json=_pbs_payload())
    assert response.status_code == 429
    assert response.json()["detail"]["code"] == "too_many_attempts"


def test_old_pbs_is_refused_before_any_change(client, monkeypatch):
    pbs = FakePbs(version="3.2.7")
    monkeypatch.setattr(
        "app.api.routes.setup.provision_pbs_access", functools.partial(provision_pbs_access, transport=pbs.transport())
    )
    _login(client)
    response = client.post("/api/v1/setup/pbs/provision", json=_pbs_payload())
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["code"] == "pbs_too_old" and "3.3" in detail["message"]
    assert "pbo@pbs" not in pbs.users and not pbs.acls


def test_fingerprint_must_be_confirmed(client, fake_pbs):
    _login(client)
    response = client.post("/api/v1/setup/pbs/provision", json=_pbs_payload(fingerprint_confirmed=False))
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "fingerprint_not_confirmed"
    assert fake_pbs.log == []


def test_pbs_error_during_provisioning_does_not_leak_password(db, caplog):
    caplog.set_level(logging.DEBUG)

    def handler(request):
        if request.url.path.endswith("access/ticket"):
            return httpx.Response(200, json={"data": {"ticket": "t", "CSRFPreventionToken": "c"}})
        if request.url.path.endswith("version"):
            return httpx.Response(200, json={"data": {"version": "4.0.1"}})
        return httpx.Response(500, json={"data": None, "message": "boom"})

    from app.services.pbs_provisioning import PbsProvisionError

    with pytest.raises(PbsProvisionError) as info:
        provision_pbs_access("10.0.0.5", PBS_FP, ROOT_PASSWORD, transport=httpx.MockTransport(handler))
    assert info.value.code == "pbs_error" and "boom" in str(info.value)
    assert info.value.__cause__ is None
    import traceback

    assert ROOT_PASSWORD not in "".join(traceback.format_exception(info.value))
    assert ROOT_PASSWORD not in caplog.text


def test_password_field_is_hidden_in_reprs():
    from app.schemas.setup import PbsProvisionRequest

    request = PbsProvisionRequest(**_pbs_payload())
    assert ROOT_PASSWORD not in repr(request) and ROOT_PASSWORD not in str(request.model_dump())


# --- Manual configuration --------------------------------------------------------


def test_manual_commands_match_the_agent_privileges(client):
    _login(client)
    commands = client.get("/api/v1/setup/manual-commands").json()
    agent_source = (Path(__file__).resolve().parents[2] / "agent" / "src" / "agent" / "pve_access.py").read_text()
    block = re.search(r"PBO_PRIVILEGES = \((.*?)\n\)", agent_source, re.DOTALL).group(1)
    agent_privileges = re.findall(r'"([A-Za-z.]+)"', block)
    assert tuple(agent_privileges) == setup_wizard.PVE_PRIVILEGES
    assert f'--privs "{",".join(agent_privileges)}"' in commands["pve"][0]
    assert any("acl update /system/disks Admin --auth-id 'pbo@pbs!pbo'" in line for line in commands["pbs"])


def test_manual_pve_save_without_pbs_vm(client, db):
    _login(client)
    response = client.put(
        "/api/v1/setup/proxmox",
        json={"api_url": "10.0.0.1", "token_id": "pbo@pve!pbo", "token_secret": "s", "fingerprint": PVE_FP, "node_name": "pve"},
    )
    assert response.status_code == 200, response.text
    assert client.get("/api/v1/setup/progress").json()["data"]["pve"]["manual"] is True


def test_every_setup_error_code_is_translated_by_the_ui():
    """Codes raised by /setup must be in the UI list (translated FR/EN, checked
    by apps/web/tests/setupWizard.test.ts)."""
    app_dir = Path(__file__).resolve().parents[1] / "app"
    sources = "".join(
        (app_dir / name).read_text(encoding="utf-8")
        for name in (
            "api/routes/setup.py",
            "services/setup_wizard.py",
            "services/setup_probe.py",
            "services/pbs_provisioning.py",
        )
    )
    raised = set(re.findall(r'(?:SetupError|PbsProvisionError)\(\s*"([a-z_]+)"', sources))
    raised |= set(re.findall(r'code="([a-z_]+)"', sources))
    raised |= set(re.findall(r'"code": "([a-z_]+)"', sources))
    web_test = Path(__file__).resolve().parents[2] / "web" / "tests" / "setupWizard.test.ts"
    block = re.search(r"SETUP_ERROR_CODES = \[(.*?)\];", web_test.read_text(encoding="utf-8"), re.DOTALL).group(1)
    translated = set(re.findall(r'"([a-z_]+)"', block))
    assert len(raised) > 20
    assert raised <= translated, sorted(raised - translated)
