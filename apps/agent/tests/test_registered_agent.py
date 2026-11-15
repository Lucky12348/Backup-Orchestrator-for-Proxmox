"""Agent installed by pairing code: callback token, app pinning, HTTPS serving."""

import hashlib
import ssl
from unittest import TestCase
from unittest.mock import MagicMock, patch

from agent import main
from agent.main import AgentSettings, ExternalExportProgress

APP = "https://10.0.0.5/api/v1"


class CallbackTokenTests(TestCase):
    def test_reporting_uses_the_callback_token_when_registered(self):
        settings = AgentSettings(server_token="inbound", callback_token="outbound", api_base_url=APP)
        with patch("agent.main.post_json") as post_json:
            main.post_heartbeat(settings)
        self.assertEqual(post_json.call_args.kwargs["token"], "outbound")

    def test_legacy_reporting_keeps_the_server_token(self):
        settings = AgentSettings(server_token="inbound", api_base_url=APP)
        self.assertEqual(settings.reporting_token, "inbound")

    def test_progress_uses_own_callback_token_only_towards_own_app(self):
        settings = AgentSettings(server_token="inbound", callback_token="outbound", api_base_url=APP)
        self.assertEqual(ExternalExportProgress(settings, 1).callback_token, "outbound")
        own = ExternalExportProgress(settings, 1, f"{APP}/external-backups/runs/1/log")
        self.assertEqual(own.callback_token, "outbound")
        foreign = ExternalExportProgress(settings, 1, "https://evil.example/api/v1/x")
        self.assertIsNone(foreign.callback_token)
        explicit = ExternalExportProgress(settings, 1, None, "from-payload")
        self.assertEqual(explicit.callback_token, "from-payload")

    def test_legacy_progress_never_falls_back_to_server_token(self):
        settings = AgentSettings(server_token="inbound", api_base_url=APP)
        self.assertIsNone(ExternalExportProgress(settings, 1).callback_token)


class AppPinningTests(TestCase):
    def test_no_fingerprint_keeps_default_verification(self):
        self.assertIs(main.app_tls_verify(AgentSettings()), True)

    def test_fingerprint_builds_a_pinned_context(self):
        context = main.app_tls_verify(AgentSettings(api_fingerprint="ab" * 32))
        self.assertIsInstance(context, main.PinnedAppSSLContext)
        self.assertEqual(context.pinned, ":".join(["AB"] * 32))
        self.assertEqual(context.verify_mode, ssl.CERT_NONE)

    def test_mismatch_closes_the_socket(self):
        context = main.PinnedAppSSLContext.create("00" * 32)
        fake = MagicMock()
        fake.getpeercert.return_value = b"cert"
        with patch.object(ssl.SSLContext, "wrap_socket", return_value=fake):
            with self.assertRaises(ssl.SSLCertVerificationError):
                context.wrap_socket(MagicMock())
        fake.close.assert_called_once()
        good = main.PinnedAppSSLContext.create(hashlib.sha256(b"cert").hexdigest())
        with patch.object(ssl.SSLContext, "wrap_socket", return_value=fake):
            self.assertIs(good.wrap_socket(MagicMock()), fake)


class ServeTlsTests(TestCase):
    def test_serves_https_when_certificate_configured(self):
        settings = AgentSettings(server_host="10.0.0.2", server_port=8090, tls_cert_file="c.pem", tls_key_file="k.pem")
        with patch("agent.main.uvicorn.run") as run:
            main.serve_http_api(settings)
        self.assertEqual(run.call_args.kwargs["ssl_certfile"], "c.pem")
        self.assertEqual(run.call_args.kwargs["ssl_keyfile"], "k.pem")
        self.assertEqual(run.call_args.kwargs["host"], "10.0.0.2")

    def test_half_configured_tls_is_refused(self):
        with self.assertRaises(SystemExit), patch("agent.main.uvicorn.run"):
            main.serve_http_api(AgentSettings(tls_cert_file="c.pem"))

    def test_legacy_plain_http_unchanged(self):
        with patch("agent.main.uvicorn.run") as run:
            main.serve_http_api(AgentSettings())
        self.assertNotIn("ssl_certfile", run.call_args.kwargs)
