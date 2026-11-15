from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from app.web_static import mount_frontend, resolve_dist_dir


def _app(dist):
    app = FastAPI()
    api = APIRouter(prefix="/api/v1")

    @api.get("/ping")
    def ping():
        return {"pong": True}

    app.include_router(api)
    mount_frontend(app, resolve_dist_dir(str(dist)))
    return TestClient(app)


def _dist(tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>spa</html>", encoding="utf-8")
    (dist / "assets" / "app-123.js").write_text("console.log(1)", encoding="utf-8")
    (dist / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("nope", encoding="utf-8")
    return dist


def test_serves_index_assets_and_spa_routes(tmp_path):
    client = _app(_dist(tmp_path))

    root = client.get("/")
    assert root.status_code == 200 and "spa" in root.text
    assert root.headers["x-frame-options"] == "DENY"

    asset = client.get("/assets/app-123.js")
    assert asset.text == "console.log(1)"
    assert "immutable" in asset.headers["cache-control"]

    assert client.get("/favicon.svg").text == "<svg/>"
    assert "spa" in client.get("/disks").text  # client-side route
    assert client.get("/api/v1/ping").json() == {"pong": True}


def test_api_paths_are_not_swallowed_and_traversal_is_blocked(tmp_path):
    client = _app(_dist(tmp_path))
    missing = client.get("/api/v1/does-not-exist")
    assert missing.status_code == 404 and missing.json() == {"detail": "Not Found"}
    assert client.get("/health-check-typo").status_code == 404
    leaked = client.get("/..%2Fsecret.txt")
    assert "nope" not in leaked.text


def test_missing_build_serves_api_only(tmp_path):
    assert resolve_dist_dir(str(tmp_path / "absent")) is None
    app = FastAPI()
    mount_frontend(app, None)
    assert TestClient(app).get("/").status_code == 404
