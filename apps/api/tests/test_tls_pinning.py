"""Certificate pinning against a real local TLS server (self-signed)."""

import datetime
import json
import ssl
import threading
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from app.core.config import Settings
from app.core.tls import build_tls_verify, certificate_fingerprint
from app.services.pbs_client import PBSApiError, PBSClient
from app.services.proxmox_client import ProxmoxClient
from app.services.setup_probe import ProbeError, probe_pbs


def _self_signed(tmp_path):
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "pbs.test")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = tmp_path / "cert.pem", tmp_path / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    )
    return cert_path, key_path, certificate_fingerprint(cert.public_bytes(serialization.Encoding.DER))


@pytest.fixture
def tls_server(tmp_path):
    cert_path, key_path, fingerprint = _self_signed(tmp_path)
    seen: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            seen.append({"path": self.path, "authorization": self.headers.get("Authorization")})
            body = json.dumps({"data": {"version": "4.1.0"} if self.path.endswith("/version") else []}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # silence
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_path, key_path)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield {"url": f"https://127.0.0.1:{server.server_address[1]}/api2/json", "fingerprint": fingerprint, "seen": seen}
    server.shutdown()
    server.server_close()


WRONG = "00:" * 31 + "00"


def test_correct_pin_connects_and_sends_token(tls_server):
    verify = build_tls_verify(False, tls_server["fingerprint"])
    with httpx.Client(verify=verify) as client:
        response = client.get(tls_server["url"] + "/version", headers={"Authorization": "secret-token"})
    assert response.status_code == 200
    assert tls_server["seen"][0]["authorization"] == "secret-token"


def test_wrong_pin_aborts_in_handshake_before_any_request(tls_server):
    verify = build_tls_verify(False, WRONG)
    with httpx.Client(verify=verify) as client, pytest.raises(httpx.ConnectError, match="fingerprint mismatch"):
        client.get(tls_server["url"] + "/version", headers={"Authorization": "secret-token"})
    assert tls_server["seen"] == []  # the token never left the client


def test_ca_verification_still_rejects_self_signed_even_with_pin(tls_server):
    verify = build_tls_verify(True, tls_server["fingerprint"])
    with httpx.Client(verify=verify) as client, pytest.raises(httpx.ConnectError):
        client.get(tls_server["url"] + "/version")
    assert tls_server["seen"] == []


def test_pbs_client_enforces_stored_fingerprint_on_every_request(tls_server):
    base = Settings(pbs_api_url=tls_server["url"], pbs_token_id="pbo@pbs!t", pbs_token_secret="s", pbs_verify_ssl=False)
    assert PBSClient(replace(base, pbs_fingerprint=tls_server["fingerprint"])).get_version()["version"] == "4.1.0"
    with pytest.raises(PBSApiError, match="fingerprint mismatch"):
        PBSClient(replace(base, pbs_fingerprint=WRONG)).get_version()
    assert len(tls_server["seen"]) == 1


def test_proxmox_client_enforces_stored_fingerprint(tls_server):
    settings = Settings(
        pve_api_url=tls_server["url"],
        pve_api_token_id="root@pam!t",
        pve_api_token_secret="s",
        pve_verify_ssl=False,
        pve_fingerprint=WRONG,
    )
    with pytest.raises(httpx.ConnectError, match="fingerprint mismatch"):
        ProxmoxClient(settings)._get("version")
    assert tls_server["seen"] == []


def test_probe_trust_on_first_use_reports_fingerprint_without_token(tls_server):
    result = probe_pbs(tls_server["url"], "pbo@pbs!t", "secret", verify_ssl=False)
    assert result.trust_required is True
    assert result.fingerprint == tls_server["fingerprint"]
    assert [entry["authorization"] for entry in tls_server["seen"]] == [None]

    pinned = probe_pbs(tls_server["url"], "pbo@pbs!t", "secret", verify_ssl=False, fingerprint=result.fingerprint)
    assert pinned.trust_required is False and pinned.version == "4.1.0"

    with pytest.raises(ProbeError, match="fingerprint mismatch"):
        probe_pbs(tls_server["url"], "pbo@pbs!t", "secret", verify_ssl=False, fingerprint=WRONG)
