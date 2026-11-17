"""Damaged versions on a rotation disk (B47, Phase 5f.4): what a failed PBS
verify found, and the repair plan.

A verify of the disk's datastore lists the snapshots it could not verify
("Failed to verify the following snapshots/groups:"). They are recorded on
the disk (`damaged_snapshots`) with whether the SOURCE datastore still holds
them.

Repair = delete the damaged versions on the DISK's datastore only (never the
source, `PBSApiBackend._delete_snapshots` refuses it), sync again, verify the
re-copied versions. PBS's sync skips a version older than the newest one of
its group already on the target ("older than the newest snapshot present on
sync target"), so a damaged version older than an intact one would not come
back: the newer intact versions of that group that the source still holds are
deleted too and copied again. When such a newer version exists only on the
disk, it is kept and the older damaged one stays "blocked" (said, not hidden).
A damaged version the source no longer holds exists only in this damaged form:
it is deleted only with an explicit confirmation.

`resync-corrupt` (sync job option, PBS 4.x) re-pulls snapshots whose last
verification failed; it is not used: a 0-byte manifest carries no readable
state, and the behaviour on such snapshots is not documented.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

_FAILED_HEADER = "Failed to verify the following snapshots/groups:"
_SNAPSHOT_RE = re.compile(r"^(?:[^:\s]+:)?(?P<type>vm|ct|host)/(?P<id>[^/\s]+)/(?P<time>\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ)$")
_REASON_RE = re.compile(r"^verify (?:[^:\s]+:)?(?P<snap>(?:vm|ct|host)/[^/\s]+/\S+Z) - (?P<reason>.+)$")


def _epoch(text: str) -> int:
    return int(datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp())


def _strip_pbs_prefix(line: str) -> str:
    """'2026-11-17T05:32:13+01:00: text' → 'text' (task log lines may carry their time)."""
    match = re.match(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:[+-]\d\d:\d\d|Z):\s?(.*)$", line.strip())
    return (match.group(1) if match else line).strip()


def parse_failed_snapshots(log_lines: list[str]) -> list[dict[str, Any]]:
    """Damaged snapshots of a failed verify task: [{key, group, time, reason}]."""
    reasons: dict[str, str] = {}
    failed: list[str] = []
    in_list = False
    for raw in log_lines:
        line = _strip_pbs_prefix(raw)
        if match := _REASON_RE.match(line):
            reasons.setdefault(match.group("snap"), match.group("reason").strip())
        if line.startswith(_FAILED_HEADER):
            in_list = True
            continue
        if in_list:
            if _SNAPSHOT_RE.match(line):
                failed.append(line)
            elif line.upper().startswith("TASK"):
                in_list = False
    result = []
    for line in dict.fromkeys(failed):
        match = _SNAPSHOT_RE.match(line)
        group = f"{match.group('type')}/{match.group('id')}"
        snap = f"{group}/{match.group('time')}"
        result.append(
            {"key": f"{group}/{_epoch(match.group('time'))}", "group": group, "time": match.group("time"), "reason": reasons.get(snap)}
        )
    return result


def _time_of(key: str) -> int:
    return int(key.rsplit("/", 1)[1])


def _group_of(key: str) -> str:
    return key.rsplit("/", 1)[0]


def plan(damaged: list[dict[str, Any]], disk_keys: list[str], source_keys: set[str], *, delete_lost: bool) -> dict[str, list[str]]:
    """{"delete", "recopy", "collateral", "lost", "blocked"} snapshot keys."""
    damaged_keys = [item["key"] for item in damaged]
    lost = [key for key in damaged_keys if key not in source_keys]
    recopy = [key for key in damaged_keys if key in source_keys]
    delete = set(recopy) | (set(lost) if delete_lost else set())
    collateral: set[str] = set()
    blocked: list[str] = []
    for key in sorted(recopy, key=_time_of):
        group, when = _group_of(key), _time_of(key)
        newer = [other for other in disk_keys if _group_of(other) == group and _time_of(other) > when and other not in delete]
        if any(other not in source_keys for other in newer):
            blocked.append(key)  # a newer version exists only on the disk: PBS would skip this one
            continue
        collateral.update(newer)
        delete.update(newer)
    for key in blocked:
        delete.discard(key)
    return {
        "delete": sorted(delete, key=lambda k: (_group_of(k), _time_of(k))),
        "recopy": sorted((set(recopy) - set(blocked)) | collateral, key=lambda k: (_group_of(k), _time_of(k))),
        "collateral": sorted(collateral, key=lambda k: (_group_of(k), _time_of(k))),
        "lost": lost,
        "blocked": blocked,
    }


def describe(key: str) -> str:
    """'ct/101/1763352347' → 'ct/101 2026-11-17 04:05:47 UTC'."""
    moment = datetime.fromtimestamp(_time_of(key), tz=timezone.utc)
    return f"{_group_of(key)} {moment:%Y-%m-%d %H:%M:%S} UTC"
