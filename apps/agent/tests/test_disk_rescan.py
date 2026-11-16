"""Phase 5b: "Run detection again" (/disk/rescan) and the partitions sent
with each disk report (approval warning)."""

from unittest import TestCase
from unittest.mock import patch

from fastapi.testclient import TestClient

from agent import main, server
from agent.main import AgentSettings, partition_summary

TOKEN = {"X-Agent-Token": "test-token"}


class DiskRescanTests(TestCase):
    def setUp(self):
        self.settings = AgentSettings(server_token="test-token", role="host")
        server.app.dependency_overrides[server.get_settings] = lambda: self.settings
        self.client = TestClient(server.app)

    def tearDown(self):
        server.app.dependency_overrides.clear()

    def test_rescan_sends_a_report_now(self):
        with patch.object(main, "post_real_disk_report") as report:
            response = self.client.post("/disk/rescan", headers=TOKEN)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        report.assert_called_once_with(self.settings, track_hotplug=True)

    def test_rescan_needs_the_token_and_the_host_role(self):
        self.assertIn(self.client.post("/disk/rescan").status_code, {401, 403})
        self.settings = AgentSettings(server_token="test-token", role="pbs")
        server.app.dependency_overrides[server.get_settings] = lambda: self.settings
        self.assertEqual(self.client.post("/disk/rescan", headers=TOKEN).status_code, 404)

    def test_rescan_is_a_capability(self):
        self.assertIn("disk-rescan", main.AGENT_CAPABILITIES)


class PartitionSummaryTests(TestCase):
    def test_lists_partitions_with_filesystem_size_and_label(self):
        device = {
            "name": "sdb",
            "type": "disk",
            "children": [
                {"name": "sdb1", "type": "part", "fstype": "ntfs", "size": 2000398934016, "label": "Photos", "mountpoint": None},
                {"name": "sdb2", "type": "part", "fstype": None, "size": 16777216},
            ],
        }
        parts = partition_summary(device)
        self.assertEqual([part["name"] for part in parts], ["sdb1", "sdb2"])
        self.assertEqual(parts[0]["filesystem_type"], "ntfs")
        self.assertEqual(parts[0]["label"], "Photos")
        self.assertEqual(parts[0]["size_gb"], 1863)
        self.assertIsNone(parts[1]["filesystem_type"])

    def test_a_blank_disk_has_no_partition(self):
        self.assertEqual(partition_summary({"name": "sdc", "type": "disk"}), [])
