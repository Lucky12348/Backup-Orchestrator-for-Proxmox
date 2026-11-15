from app.auth import LOGIN_MAX_ATTEMPTS, SESSION_COOKIE_NAME
from app.models.app_setting import AppSetting
from app.models.user import User
from app.services.users import bootstrap_admin_from_env, hash_password

ADMIN = {"username": "admin", "password": "correct horse battery"}


def _set_cookie_header(response) -> str:
    return response.headers.get("set-cookie", "")


def _create_admin(client):
    return client.post("/api/v1/setup/admin", json=ADMIN)


def test_status_reports_needs_setup_until_admin_exists(client):
    status = client.get("/api/v1/setup/status").json()
    assert status == {"needs_setup": True, "proxmox_configured": False, "pbs_configured": False}

    assert _create_admin(client).status_code == 201
    assert client.get("/api/v1/setup/status").json()["needs_setup"] is False


def test_first_admin_opens_a_hardened_cookie_session(client):
    response = _create_admin(client)
    assert response.status_code == 201
    assert response.json()["username"] == "admin"
    assert "access_token" not in response.text

    cookie = _set_cookie_header(response).lower()
    assert cookie.startswith(f"{SESSION_COOKIE_NAME}=")
    for attribute in ("httponly", "secure", "samesite=strict", "path=/", "max-age=10800"):
        assert attribute in cookie

    assert client.get("/api/v1/auth/me").json()["username"] == "admin"


def test_second_admin_is_refused(client, session_factory):
    assert _create_admin(client).status_code == 201
    response = client.post("/api/v1/setup/admin", json={"username": "intruder", "password": "another long password"})
    assert response.status_code == 409
    with session_factory() as db:
        assert [user.username for user in db.query(User).all()] == ["admin"]


def test_setup_admin_validates_credentials(client):
    response = client.post("/api/v1/setup/admin", json={"username": "admin", "password": "short"})
    assert response.status_code == 422
    response = client.post("/api/v1/setup/admin", json={"username": "bad name!", "password": "long enough password"})
    assert response.status_code == 422
    assert client.get("/api/v1/setup/status").json()["needs_setup"] is True


def test_protected_routes_require_session_cookie(client):
    assert client.get("/api/v1/auth/me").status_code == 401
    assert client.get("/api/v1/settings").status_code == 401
    assert client.post("/api/v1/setup/proxmox/test", json={"api_url": "x", "token_id": "y"}).status_code == 401
    # A Bearer header is not accepted anymore: only the HttpOnly cookie.
    assert client.get("/api/v1/disks", headers={"Authorization": "Bearer whatever"}).status_code == 401


def test_login_logout_flow(client):
    _create_admin(client)
    client.cookies.clear()

    bad = client.post("/api/v1/auth/token", data={"username": "admin", "password": "wrong password"})
    assert bad.status_code == 401
    assert SESSION_COOKIE_NAME not in _set_cookie_header(bad)

    good = client.post("/api/v1/auth/token", data=ADMIN)
    assert good.status_code == 200
    assert "httponly" in _set_cookie_header(good).lower()
    assert client.get("/api/v1/auth/me").status_code == 200

    logout = client.post("/api/v1/auth/logout")
    assert logout.status_code == 204
    assert 'max-age=0' in _set_cookie_header(logout).lower()
    client.cookies.clear()
    assert client.get("/api/v1/auth/me").status_code == 401


def test_forged_or_orphan_tokens_are_rejected(client, session_factory):
    _create_admin(client)
    client.cookies.clear()
    client.cookies.set(SESSION_COOKIE_NAME, "not-a-jwt")
    assert client.get("/api/v1/auth/me").status_code == 401

    client.cookies.clear()
    client.post("/api/v1/auth/token", data=ADMIN)
    with session_factory() as db:
        db.query(User).delete()
        db.commit()
    # Valid signature but the account no longer exists.
    assert client.get("/api/v1/auth/me").status_code == 401


def test_login_lockout(client):
    _create_admin(client)
    for _ in range(LOGIN_MAX_ATTEMPTS):
        client.post("/api/v1/auth/token", data={"username": "admin", "password": "nope nope nope"})
    assert client.post("/api/v1/auth/token", data=ADMIN).status_code == 429


def test_bootstrap_admin_from_legacy_env(db):
    legacy_hash = hash_password("legacy password 123")
    user = bootstrap_admin_from_env(db, {"AUTH_USERNAME": "ops", "AUTH_PASSWORD_HASH": legacy_hash})
    assert user is not None and user.username == "ops"
    assert db.get(AppSetting, "meta.first_admin_created") is not None
    # Only once: an existing account is never replaced.
    assert bootstrap_admin_from_env(db, {"AUTH_USERNAME": "other", "AUTH_PASSWORD_HASH": legacy_hash}) is None


def test_bootstrap_ignores_truncated_compose_hash(db):
    assert bootstrap_admin_from_env(db, {"AUTH_PASSWORD_HASH": "$2b"}) is None
    assert db.query(User).count() == 0


def test_settings_endpoint_masks_and_validates(client):
    _create_admin(client)
    response = client.patch(
        "/api/v1/settings",
        json={"values": {"PBS_AGENT_TOKEN": "agent-secret", "PBS_AGENT_BASE_URL": "http://pbs:8091"}},
    )
    assert response.status_code == 200
    assert "agent-secret" not in response.text
    items = {item["key"]: item for item in response.json()}
    assert items["PBS_AGENT_TOKEN"]["stored"] is True and items["PBS_AGENT_TOKEN"]["value"] is None

    invalid = client.patch("/api/v1/settings", json={"values": {"PBS_EXECUTION_VM_ID": "abc"}})
    assert invalid.status_code == 422

    # Enabling notifications without topic is refused and nothing is written.
    refused = client.patch("/api/v1/settings", json={"values": {"NOTIFICATIONS_ENABLED": True, "NTFY_TOPIC": ""}})
    assert refused.status_code == 422
    items = {item["key"]: item for item in client.get("/api/v1/settings").json()}
    assert items["NOTIFICATIONS_ENABLED"]["value"] is False
