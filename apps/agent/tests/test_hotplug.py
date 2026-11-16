import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from agent import hotplug
from agent.main import AgentSettings, sync_state


def disk(serial, model="Samsung T7", candidate_type="usb"):
    return {"serial_number": serial, "model_name": model, "candidate_type": candidate_type, "capacity_gb": 1000}


class HotplugDiffTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name) / "state" / "usb-disks.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_first_report_is_a_baseline(self):
        self.assertEqual(hotplug.diff_and_store([disk("A")], self.state), [])
        self.assertTrue(self.state.is_file())

    def test_added_then_removed(self):
        hotplug.diff_and_store([disk("A")], self.state)
        added = hotplug.diff_and_store([disk("A"), disk("B", "WD Elements")], self.state)
        self.assertEqual(added, [{"action": "added", "serial": "B", "model": "WD Elements"}])
        removed = hotplug.diff_and_store([disk("B", "WD Elements")], self.state)
        self.assertEqual(removed, [{"action": "removed", "serial": "A", "model": "Samsung T7"}])
        self.assertEqual(hotplug.diff_and_store([disk("B", "WD Elements")], self.state), [])

    def test_only_usb_or_removable_disks_count(self):
        hotplug.diff_and_store([], self.state)
        events = hotplug.diff_and_store([disk("S", candidate_type="standalone"), disk("R", candidate_type="removable")], self.state)
        self.assertEqual([event["serial"] for event in events], ["R"])

    def test_corrupt_state_is_a_new_baseline(self):
        self.state.parent.mkdir(parents=True)
        self.state.write_text("{not json")
        self.assertEqual(hotplug.diff_and_store([disk("A")], self.state), [])

    def test_state_dir_from_systemd(self):
        with patch.dict("os.environ", {"STATE_DIRECTORY": "/var/lib/pbo-agent", "AGENT_STATE_DIR": ""}):
            self.assertEqual(hotplug.state_file().as_posix(), "/var/lib/pbo-agent/usb-disks.json")


class SettleTests(TestCase):
    def test_settle_waits_then_runs_udevadm(self):
        calls = []
        with patch("agent.hotplug.shutil.which", return_value="/usr/bin/udevadm"):
            hotplug.settle(2.0, sleep=lambda s: calls.append(("sleep", s)), run=lambda cmd, **kw: calls.append(("run", cmd)))
        self.assertEqual(calls[0], ("sleep", 2.0))
        self.assertEqual(calls[1][1][:2], ["udevadm", "settle"])

    def test_burst_of_events_gives_one_report(self):
        """Debounce: the unit is started by every udev event, but systemd merges
        the starts that arrive while it is settling; one run = one report with
        every change of the burst."""
        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ", {"AGENT_STATE_DIR": tmp}):
            hotplug.diff_and_store([], None)  # baseline before the burst
            posted = []
            # Three disks plugged together (one burst): discovered in one pass.
            with patch("agent.main.discover_real_disks", return_value=[disk("A"), disk("B"), disk("C")]), patch(
                "agent.main.post_json", side_effect=lambda settings, path, payload, token=None: posted.append((path, payload))
            ), patch("agent.hotplug.settle") as settle:
                sync_state(AgentSettings(hostname="pve"), settle_seconds=2.0)
            settle.assert_called_once_with(2.0)
            reports = [payload for path, payload in posted if path == "/agent/disks/report"]
            self.assertEqual(len(reports), 1)
            self.assertEqual([event["serial"] for event in reports[0]["hotplug"]], ["A", "B", "C"])
            self.assertEqual(reports[0]["trigger"], "udev")

    def test_timer_report_without_change(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ", {"AGENT_STATE_DIR": tmp}):
            hotplug.diff_and_store([disk("A")], None)
            posted = []
            with patch("agent.main.discover_real_disks", return_value=[disk("A")]), patch(
                "agent.main.post_json", side_effect=lambda settings, path, payload, token=None: posted.append((path, payload))
            ):
                sync_state(AgentSettings(hostname="pve"))
            report = next(payload for path, payload in posted if path == "/agent/disks/report")
            self.assertEqual((report["hotplug"], report["trigger"]), ([], "timer"))
