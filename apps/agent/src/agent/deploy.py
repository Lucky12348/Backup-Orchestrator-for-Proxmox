"""Files the agent installs on its host: systemd units and the udev rule.

Single source for the one-line installer (`install.sh` runs
`python3 -m agent.deploy write --role host`) and for the self-update
(`agent.self_update`), so a new agent version brings its own units.
Standard library only: the installer runs it with the system python3.

Code layout (since the self-update):

    /opt/pbo-agent/releases/<build_id>/   extracted agent archive (BUILD_ID, src/…)
    /opt/pbo-agent/current -> releases/<build_id>   switched atomically
    /opt/pbo-agent/venv/                  dependencies (fastapi, httpx, uvicorn)

The units run `venv/bin/python -m agent.main` with PYTHONPATH on
`current/src`: switching the symlink and restarting is the whole update.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Layout:
    install_dir: Path = Path("/opt/pbo-agent")
    env_file: Path = Path("/etc/pbo-agent/agent.env")
    systemd_dir: Path = Path("/etc/systemd/system")
    udev_rule_file: Path = Path("/etc/udev/rules.d/90-pbo-agent.rules")
    state_dir: Path = Path("/var/lib/pbo-agent")

    @property
    def releases(self) -> Path:
        return self.install_dir / "releases"

    @property
    def current(self) -> Path:
        return self.install_dir / "current"

    @property
    def python(self) -> Path:
        return self.install_dir / "venv" / "bin" / "python"


UDEV_RULE = """\
# Proxmox Backup Orchestrator agent (installed by pbo-agent-install.sh).
# Report USB disks as soon as they are plugged in or unplugged.
ACTION=="add", SUBSYSTEM=="block", ENV{DEVTYPE}=="disk", ENV{ID_BUS}=="usb", TAG+="systemd", ENV{SYSTEMD_WANTS}+="pbo-agent-report.service"
ACTION=="remove", SUBSYSTEM=="block", ENV{DEVTYPE}=="disk", ENV{ID_BUS}=="usb", RUN+="/usr/bin/systemctl --no-block start pbo-agent-report.service"
# A disk passed through to the PBS VM has no block device on the host: only
# the USB device event tells it was unplugged.
ACTION=="remove", SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", RUN+="/usr/bin/systemctl --no-block start pbo-agent-report.service"
ACTION=="add", SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", RUN+="/usr/bin/systemctl --no-block start pbo-agent-report.service"
"""


def agent_service(role: str, layout: Layout = Layout()) -> str:
    return f"""\
[Unit]
Description=Proxmox Backup Orchestrator agent ({role})
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
# Root is required for qm (USB passthrough) and disk inspection.
User=root
WorkingDirectory={layout.install_dir.as_posix()}
EnvironmentFile={layout.env_file.as_posix()}
# The code of the active release (switched atomically by the self-update).
Environment=PYTHONPATH={layout.current.as_posix()}/src
ExecStart={layout.python.as_posix()} -m agent.main serve
Restart=on-failure
RestartSec=5
NoNewPrivileges=yes
PrivateTmp=yes
ProtectHome=yes
RestrictSUIDSGID=yes
LockPersonality=yes

[Install]
WantedBy=multi-user.target
"""


def report_service(layout: Layout = Layout()) -> str:
    return f"""\
[Unit]
Description=Proxmox Backup Orchestrator agent heartbeat and disk report
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
User=root
WorkingDirectory={layout.install_dir.as_posix()}
EnvironmentFile={layout.env_file.as_posix()}
Environment=PYTHONPATH={layout.current.as_posix()}/src
# Started by the timer and by {layout.udev_rule_file.as_posix()} (USB disk add/remove).
# --settle 2: wait for the partitions, then udevadm settle. Start requests
# arriving meanwhile are merged into this run by systemd (debounce).
ExecStart={layout.python.as_posix()} -m agent.main sync-state --settle 2
StateDirectory=pbo-agent
TimeoutStartSec=120
NoNewPrivileges=yes
PrivateTmp=yes
ProtectHome=yes
"""


REPORT_TIMER = """\
[Unit]
Description=Proxmox Backup Orchestrator agent report (fallback, every 5 minutes)

[Timer]
OnBootSec=1min
OnUnitActiveSec=5min
AccuracySec=15s
Unit=pbo-agent-report.service
Persistent=true

[Install]
WantedBy=timers.target
"""


def deploy_files(role: str, layout: Layout = Layout()) -> dict[Path, str]:
    """Every file the agent owns outside its release directory."""
    files = {
        layout.systemd_dir / "pbo-agent.service": agent_service(role, layout),
        layout.systemd_dir / "pbo-agent-report.service": report_service(layout),
        layout.systemd_dir / "pbo-agent-report.timer": REPORT_TIMER,
    }
    if role == "host":
        files[layout.udev_rule_file] = UDEV_RULE
    return files


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.pbo-tmp")
    tmp.write_text(content, encoding="utf-8")
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def write_files(files: dict[Path, str]) -> dict[Path, str | None]:
    """Writes each file atomically; returns what was there before (None =
    absent) so a failed update can put it back (`restore_files`)."""
    previous: dict[Path, str | None] = {}
    for path, content in files.items():
        previous[path] = path.read_text(encoding="utf-8") if path.exists() else None
        _atomic_write(path, content)
    return previous


def restore_files(previous: dict[Path, str | None]) -> None:
    for path, content in previous.items():
        if content is None:
            path.unlink(missing_ok=True)
        else:
            _atomic_write(path, content)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m agent.deploy")
    sub = parser.add_subparsers(dest="command", required=True)
    write = sub.add_parser("write", help="write the systemd units (and the udev rule for the host role)")
    write.add_argument("--role", choices=("host", "pbs"), required=True)
    write.add_argument("--install-dir", default=str(Layout.install_dir))
    write.add_argument("--env-file", default=str(Layout.env_file))
    write.add_argument("--systemd-dir", default=str(Layout.systemd_dir))
    write.add_argument("--udev-rule-file", default=str(Layout.udev_rule_file))
    sub.add_parser("udev-rule", help="print the udev rule")
    args = parser.parse_args(argv)
    if args.command == "udev-rule":
        sys.stdout.write(UDEV_RULE)
        return 0
    layout = Layout(
        install_dir=Path(args.install_dir),
        env_file=Path(args.env_file),
        systemd_dir=Path(args.systemd_dir),
        udev_rule_file=Path(args.udev_rule_file),
    )
    for path in write_files(deploy_files(args.role, layout)):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
