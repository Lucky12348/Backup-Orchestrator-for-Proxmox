from unittest import TestCase
from unittest.mock import patch

from fastapi.testclient import TestClient

from agent import server
from agent.main import AgentSettings

TOKEN = {"X-Agent-Token": "test-token"}


class AgentRoleTests(TestCase):
    def _client(self, role: str) -> TestClient:
        settings = AgentSettings(server_token="test-token", role=role)
        server.app.dependency_overrides[server.get_settings] = lambda: settings
        return TestClient(server.app)  # lifespan not run: no PBS probing

    def tearDown(self):
        server.app.dependency_overrides.clear()

    def test_every_served_route_has_a_role(self):
        served = {route.path for route in server.app.routes if getattr(route, "endpoint", None) and route.path.startswith("/") and route.path not in {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}}
        self.assertEqual(served - set(server.ROUTE_ROLES), set())

    def test_host_role_refuses_pbs_routes(self):
        client = self._client("host")
        response = client.post("/run-external-export", json={}, headers=TOKEN)
        self.assertEqual(response.status_code, 404)
        self.assertIn("AGENT_ROLE=host", response.json()["detail"])
        self.assertEqual(client.post("/eject-dedicated-pbs-datastore", json={}, headers=TOKEN).status_code, 404)
        self.assertEqual(client.post("/inspect-disk", json={"disk": "sdb"}, headers=TOKEN).status_code, 404)

    def test_pbs_role_refuses_host_routes(self):
        client = self._client("pbs")
        with patch("agent.server.qemu_usb_attach_result") as attach:
            response = client.post("/qemu/usb/attach", json={"vmid": 100, "slot": "usb0", "host": "1-2"}, headers=TOKEN)
        self.assertEqual(response.status_code, 404)
        attach.assert_not_called()
        self.assertEqual(client.get("/notifications/templates", headers=TOKEN).status_code, 404)

    def test_shared_routes_and_version_listing(self):
        client = self._client("pbs")
        version = client.get("/version", headers=TOKEN).json()
        self.assertEqual(version["role"], "pbs")
        self.assertIn("/run-external-export", version["routes"])
        self.assertNotIn("/qemu/usb/attach", version["routes"])

    def test_token_is_checked_before_role(self):
        client = self._client("host")
        self.assertEqual(client.post("/run-external-export", json={}).status_code, 401)

    def test_no_role_keeps_legacy_behaviour(self):
        self.assertTrue(server.route_allowed("/qemu/usb/attach", ""))
        self.assertTrue(server.route_allowed("/run-external-export", ""))
        self.assertFalse(server.route_allowed("/unknown", "host"))

    def test_invalid_role_is_rejected(self):
        with self.assertRaises(RuntimeError):
            server.validate_role("both")
        server.validate_role("")
        server.validate_role("pbs")
