"""B41: stale mounts stacked on a removable datastore path (PBS role)."""

import subprocess
import tempfile
import unittest
from pathlib import Path

from agent.mounts import cleanup_dead_mounts, mounts_at, validate_path

PATH = "/mnt/datastore/pbo-pbotesta"


def mountinfo_line(mount_id, source, options="rw,relatime", super_options="rw"):
    return f"{mount_id} 1 8:33 / {PATH} {options} shared:1 - ext4 {source} {super_options}"


class Stack:
    """A simulated mount stack: `umount -l` drops the top layer."""

    def __init__(self, tmp: Path, layers):
        self.file = tmp / "mountinfo"
        self.layers = list(layers)
        self.calls = []
        self.write()

    def write(self):
        lines = ["22 1 252:1 / / rw - ext4 /dev/mapper/pbs-root rw"] + [mountinfo_line(*layer) for layer in self.layers]
        self.file.write_text("\n".join(lines) + "\n")

    def run(self, args, **kwargs):
        self.calls.append(args)
        self.layers.pop()
        self.write()
        return subprocess.CompletedProcess(args, 0, "", "")


class MountsTests(unittest.TestCase):
    def test_real_case_two_dead_layers_are_removed_lazily(self):
        with tempfile.TemporaryDirectory() as tmp:
            # sdc1 then sdd1, both gone, ext4 shut down.
            stack = Stack(Path(tmp), [("100", "/dev/sdc1", "rw,relatime", "rw,shutdown"), ("101", "/dev/sdd1", "rw,relatime", "rw,shutdown")])
            result = cleanup_dead_mounts(PATH, run=stack.run, mountinfo=stack.file, exists=lambda source: False)
            self.assertEqual(stack.calls, [["umount", "-l", PATH], ["umount", "-l", PATH]])
            self.assertEqual([item["source"] for item in result["removed"]], ["/dev/sdd1", "/dev/sdc1"])
            self.assertEqual((result["remaining"], result["live_mount"]), ([], False))

    def test_a_live_mount_is_never_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            stack = Stack(Path(tmp), [("100", "/dev/sdc1", "rw", "rw,shutdown"), ("101", "/dev/sde1", "rw", "rw")])
            result = cleanup_dead_mounts(PATH, run=stack.run, mountinfo=stack.file, exists=lambda source: source == "/dev/sde1")
            self.assertEqual(stack.calls, [])  # live on top: untouched
            self.assertEqual((result["live_mount"], result["hidden_dead"]), (True, 1))

    def test_shutdown_option_or_missing_device_is_dead(self):
        with tempfile.TemporaryDirectory() as tmp:
            stack = Stack(Path(tmp), [("100", "/dev/sdc1", "rw", "rw,shutdown")])
            (layer,) = mounts_at(PATH, mountinfo=stack.file, exists=lambda source: True)
            self.assertTrue(layer["dead"] and layer["shutdown"] and layer["device_exists"])

    def test_only_datastore_paths(self):
        for path in ("/", "/mnt/datastore", "/mnt/datastore/../../etc", "/mnt/datastore/a/b"):
            with self.assertRaises(ValueError):
                validate_path(path)


if __name__ == "__main__":
    unittest.main()
