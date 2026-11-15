"""The pinned HTTPS helper embedded in app/agent_install/install.sh, run for
real against a local self-signed TLS server."""

import json
import os
import ssl
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.services.agent_bootstrap import INSTALL_SCRIPT_TEMPLATE
from tests.test_tls_pinning import WRONG, _self_signed


@pytest.fixture
def helper(tmp_path):
    script = INSTALL_SCRIPT_TEMPLATE.read_text(encoding="utf-8")
    body = script.split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
    path = tmp_path / "pbo_https.py"
    path.write_text(body, encoding="utf-8")
    return path


@pytest.fixture
def app_server(tmp_path):
    cert_path, key_path, fingerprint = _self_signed(tmp_path)
    seen: list[dict] = []
    registration = {"status": 201}

    class Handler(BaseHTTPRequestHandler):
        def _send(self, status, body, content_type="application/json"):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802
            seen.append({"method": "GET", "path": self.path})
            self._send(200, b"archive-bytes", "application/gzip")

        def do_POST(self):  # noqa: N802
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append({"method": "POST", "path": self.path, "payload": payload})
            if registration["status"] != 201:
                self._send(registration["status"], json.dumps({"detail": "Invalid or expired pairing code."}).encode())
                return
            self._send(
                201,
                json.dumps(
                    {
                        "agent_id": 3,
                        "role": "host",
                        "agent_token": "AgentTok_123-x",
                        "callback_token": "CbTok_456-y",
                        "api_base_url": "https://10.0.0.5/api/v1",
                    }
                ).encode(),
            )

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_path, key_path)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield {
        "url": f"https://127.0.0.1:{server.server_address[1]}",
        "fingerprint": fingerprint,
        "seen": seen,
        "registration": registration,
    }
    server.shutdown()
    server.server_close()


def _run(helper, *args):
    return subprocess.run([sys.executable, "-I", str(helper), *args], capture_output=True, text=True, timeout=30)


def test_get_with_the_right_pin_downloads(helper, app_server, tmp_path):
    out = tmp_path / "agent.tar.gz"
    result = _run(helper, "get", f"{app_server['url']}/agent/agent.tar.gz", app_server["fingerprint"], str(out))
    assert result.returncode == 0, result.stderr
    assert out.read_bytes() == b"archive-bytes"


def test_wrong_pin_sends_nothing(helper, app_server, tmp_path):
    result = _run(helper, "get", f"{app_server['url']}/agent/agent.tar.gz", WRONG, str(tmp_path / "x"))
    assert result.returncode != 0
    assert "fingerprint mismatch" in result.stderr
    assert app_server["seen"] == []


def _register(helper, app_server, env_file, fingerprint=None):
    return _run(
        helper,
        "register",
        f"{app_server['url']}/api/v1/agents/register",
        fingerprint or app_server["fingerprint"],
        str(env_file),
        "ABCD-EFGH",
        "host",
        "pve1",
        "https://192.168.1.10:8090",
        "AB" * 32,
        "AGENT_ROLE=host",
        "AGENT_SERVER_HOST=192.168.1.10",
    )


def test_register_writes_the_env_file(helper, app_server, tmp_path):
    env_file = tmp_path / "agent.env"
    result = _register(helper, app_server, env_file)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "3"
    assert app_server["seen"][0]["payload"] == {
        "code": "ABCD-EFGH",
        "role": "host",
        "hostname": "pve1",
        "base_url": "https://192.168.1.10:8090",
        "fingerprint": "AB" * 32,
    }
    lines = dict(
        line.split("=", 1) for line in env_file.read_text().splitlines() if line and not line.startswith("#")
    )
    assert lines == {
        "AGENT_SERVER_TOKEN": "AgentTok_123-x",
        "AGENT_CALLBACK_TOKEN": "CbTok_456-y",
        "AGENT_API_BASE_URL": "https://10.0.0.5/api/v1",
        "AGENT_API_FINGERPRINT": app_server["fingerprint"],
        "AGENT_ROLE": "host",
        "AGENT_SERVER_HOST": "192.168.1.10",
    }
    if os.name == "posix":
        assert (env_file.stat().st_mode & 0o777) == 0o600


def test_refused_registration_writes_nothing(helper, app_server, tmp_path):
    app_server["registration"]["status"] = 401
    env_file = tmp_path / "agent.env"
    result = _register(helper, app_server, env_file)
    assert result.returncode != 0
    assert "Invalid or expired pairing code" in result.stderr
    assert not env_file.exists()


def test_register_with_wrong_pin_never_sends_the_code(helper, app_server, tmp_path):
    result = _register(helper, app_server, tmp_path / "agent.env", fingerprint=WRONG)
    assert result.returncode != 0
    assert app_server["seen"] == []
