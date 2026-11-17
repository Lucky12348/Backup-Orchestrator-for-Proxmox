"""B41: stale mounts stacked on a removable datastore path inside the PBS VM,
handled by the host agent through the QEMU guest agent (`qm guest exec`)."""

import json
import subprocess
import unittest

from agent.mounts import cleanup_dead_mounts, guest_agent_status, validate_path

PATH = "/mnt/datastore/pbo-pbotesta"
ROOT = "22 1 252:1 / / rw - ext4 /dev/mapper/pbs-root rw"


def layer(mount_id, source, super_options="rw"):
    return f"{mount_id} 1 8:33 / {PATH} rw,relatime shared:1 - ext4 {source} {super_options}"


class FakeGuest:
    """`qm` on the host: a PBS VM whose mount stack and devices are simulated."""

    def __init__(self, layers, devices=(), agent=True, agent_config="agent: 1"):
        self.layers = list(layers)
        self.devices = set(devices)
        self.agent = agent
        self.agent_config = agent_config
        self.calls = []

    def __call__(self, command, **kwargs):
        self.calls.append(command)
        if command[:2] == ["qm", "config"]:
            return subprocess.CompletedProcess(command, 0, f"{self.agent_config}\nmemory: 2048\n", "")
        if command[:3] == ["qm", "guest", "cmd"]:
            return subprocess.CompletedProcess(command, 0 if self.agent else 255, "", "" if self.agent else "QEMU guest agent is not running")
        if not self.agent:
            return subprocess.CompletedProcess(command, 255, "", "QEMU guest agent is not running")
        argv = command[command.index("--") + 1 :]
        if argv == ["cat", "/proc/self/mountinfo"]:
            out = "\n".join([ROOT] + [layer(*item) for item in self.layers])
        elif argv[:2] == ["sh", "-c"]:
            out = "\n".join(device for device in argv[4:] if device in self.devices)
        elif argv == ["umount", "-l", PATH]:
            self.layers.pop()
            out = ""
        else:
            raise AssertionError(argv)
        return subprocess.CompletedProcess(command, 0, json.dumps({"exitcode": 0, "exited": 1, "out-data": out}), "")


class GuestMountsTests(unittest.TestCase):
    def test_real_case_two_dead_layers_are_removed_from_the_top(self):
        guest = FakeGuest([("100", "/dev/sdc1", "rw,shutdown"), ("101", "/dev/sdd1", "rw,shutdown")])
        result = cleanup_dead_mounts(100, PATH, run=guest)
        umounts = [call for call in guest.calls if call[-3:] == ["umount", "-l", PATH]]
        self.assertEqual(len(umounts), 2)
        self.assertTrue(all(call[:4] == ["qm", "guest", "exec", "100"] for call in umounts))
        self.assertEqual([item["source"] for item in result["removed"]], ["/dev/sdd1", "/dev/sdc1"])
        self.assertEqual((result["guest_agent"], result["remaining"], result["live_mount"]), (True, [], False))

    def test_a_live_mount_is_never_removed(self):
        guest = FakeGuest([("100", "/dev/sdc1", "rw,shutdown"), ("101", "/dev/sde1", "rw")], devices={"/dev/sde1"})
        result = cleanup_dead_mounts(100, PATH, run=guest)
        self.assertFalse([call for call in guest.calls if "umount" in call])
        self.assertEqual((result["live_mount"], result["hidden_dead"]), (True, 1))

    def test_a_device_gone_without_shutdown_is_dead_too(self):
        guest = FakeGuest([("100", "/dev/sdc1", "rw")], devices=set())
        self.assertEqual(cleanup_dead_mounts(100, PATH, run=guest)["removed"], [{"source": "/dev/sdc1", "reason": "device_gone"}])

    def test_without_guest_agent_nothing_is_checked(self):
        result = cleanup_dead_mounts(100, PATH, run=FakeGuest([], agent=False))
        self.assertEqual((result["guest_agent"], result["removed"]), (False, []))

    def test_guest_agent_status(self):
        self.assertEqual(
            {key: guest_agent_status(100, run=FakeGuest([]))[key] for key in ("enabled", "active")}, {"enabled": True, "active": True}
        )
        status = guest_agent_status(100, run=FakeGuest([], agent=False, agent_config="boot: order=scsi0"))
        self.assertEqual((status["enabled"], status["active"]), (False, False))

    def test_only_datastore_paths(self):
        for path in ("/", "/mnt/datastore", "/mnt/datastore/../../etc", "/mnt/datastore/a/b"):
            with self.assertRaises(ValueError):
                validate_path(path)


if __name__ == "__main__":
    unittest.main()
