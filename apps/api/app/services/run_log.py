"""Readable run journal (B22, B23), built from a run's raw stdout/stderr.

The raw log mixes three things: PBO entries (`[ISO-Z] step message`), PBS
task lines (`2026-11-16T02:40:00+01:00: text`, sometimes glued after a PBO
message), and raw blocks merged at the end of a step. This module turns it
into entries every view can show:

* progress lines (`percentage done`, `Chunkstore create: n%`) are not journal
  lines: they feed the progress bar (`progress_from_log`);
* a PBS line seen twice (same PBS timestamp + text, e.g. the live copy and the
  merged block) is kept once; identical consecutive lines collapse (`count`);
* PBO messages get a stable `code` + `params` (translated by the UI); PBS
  lines are kept verbatim and marked `source="pbs"`;
* the transfer statistics come from PBS's `Summary:` line.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_PBO_ENTRY_RE = re.compile(r"^\[(?P<at>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)\]\s+(?P<step>[a-z_]+)\s?(?P<text>.*)$")
_PBS_TS = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:[+-]\d{2}:\d{2}|Z)"
_PBS_LINE_RE = re.compile(rf"^(?P<at>{_PBS_TS}):\s?(?P<text>.*)$")
_PBS_INLINE_RE = re.compile(rf"\s(?={_PBS_TS}:\s)")

_PERCENT_DONE_RE = re.compile(r"percentage done:\s*(?P<pct>\d+(?:\.\d+)?)%", re.IGNORECASE)
_CHUNKSTORE_RE = re.compile(r"chunkstore create:\s*(?P<pct>\d+(?:\.\d+)?)%", re.IGNORECASE)
_SIZE_RE = re.compile(r"(?P<n>\d+(?:\.\d+)?)\s*(?P<unit>B|KiB|MiB|GiB|TiB)\b")
_RATE_RE = re.compile(r"(?P<n>\d+(?:\.\d+)?)\s*(?P<unit>B|KiB|MiB|GiB|TiB)/s\b")
_UNITS = {"B": 1, "KiB": 1024, "MiB": 1024**2, "GiB": 1024**3, "TiB": 1024**4}

FAILURE_STEPS = {"failure", "eject_not_finished", "usb_device_not_found", "usb_location_not_in_proxmox", "disk_not_plugged", "usb_device_ambiguous"}

# PBO messages → stable code + params (the UI holds the FR/EN texts).
# Keep in sync with `runLog` in apps/web/src/i18n.ts (a test checks it).
PBO_MESSAGES: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (code, re.compile(pattern))
    for code, pattern in (
        ("run_created", r"^External backup run created\. Background execution will start shortly\.$"),
        ("run_starting", r"^Starting external backup execution\.$"),
        ("handoff_start", r"^Handing off USB disk to PBS VM\.$"),
        ("handoff_usb_chosen", r"^USB device chosen by (?P<method>[a-z_]+): (?P<port>\S+?)\.?$"),
        ("handoff_mapping", r"^Selected USB mapping `(?P<mapping>[^`]+)` for `(?P<serial>[^`]+)`\.$"),
        ("handoff_attach_payload", r"^Attach payload: `(?P<payload>[^`]+)`\.$"),
        ("handoff_attach_response", r"^Host agent USB attach response: `(?P<detail>.+)`\.$"),
        ("handoff_verify", r"^VM config verification attempt (?P<n>\d+)/(?P<total>\d+) for `(?P<slot>[^`]+)`: `(?P<value>[^`]*)`\.$"),
        ("usb_moved", r"^Disk moved from USB port (?P<old>\S+) to (?P<new>\S+): using the new port\.$"),
        ("inspect_retry_failed", r"^PBS inspect retry (?P<n>\d+)/(?P<total>\d+) failed: (?P<reason>.+)$"),
        ("inspect_retry_identifier", r"^PBS inspect retry (?P<n>\d+)/(?P<total>\d+) used identifier `(?P<identifier>[^`]+)`\.$"),
        ("inspect_retry_ok", r"^PBS inspect retry (?P<n>\d+)/(?P<total>\d+) succeeded: (?P<device>\S+?)\.$"),
        ("success_summary", r"^Disk is now visible on PBS as (?P<device>\S+?)\. PBS local sync job completed\.$"),
        ("disk_visible", r"^Disk is now visible on PBS as (?P<device>\S+?)\.$"),
        ("inspect_disk", r"^Inspecting disk on PBS agent\.$"),
        ("prepare_datastore", r"^Preparing or reusing dedicated PBS datastore\.$"),
        ("datastore_reused", r"^Existing dedicated PBS datastore reused\. No formatting performed\.$"),
        ("guard_check", r"^Guard check: (?P<reason>.+?)\.?$"),
        ("wiping", r"^Wiping `(?P<device>[^`]+)` on PBS"),
        ("creating_datastore", r"^Creating removable datastore `(?P<store>[^`]+)` \(ext4\) on `(?P<device>[^`]+)`\.$"),
        ("datastore_marked", r"^PBO-marked datastore `(?P<store>[^`]+)` found on this disk\.$"),
        ("mounting", r"^Mounting removable datastore `(?P<store>[^`]+)`\.$"),
        ("export_start", r"^Starting PBS datastore export to dedicated disk\.$"),
        ("sync_job_create", r"^Creating local PBS sync job `(?P<job>[^`]+)`: `(?P<source>[^`]+)` → `(?P<target>[^`]+)` \(no remote\)\.$"),
        ("sync_task_started", r"^PBS sync task started: (?P<upid>\S+)$"),
        ("vm_selection", r"^Copying only: (?P<groups>.+)\.$"),
        ("vm_selection_agent_mode", r"^VM selection needs PBS_MODE=api: the whole datastore is copied\.$"),
        ("auto_ejected", r"^Disk ejected automatically after a successful backup\.$"),
        ("auto_eject_failed", r"^Automatic eject failed: (?P<reason>.+)$"),
        ("auto_eject_unsupported", r"^Automatic eject was requested, but this disk mode does not support it yet"),
        ("handoff_disk_not_plugged", r"^The disk is no longer plugged into the Proxmox host\.$"),
        ("handoff_usb_not_found", r"^Disk not found in the Proxmox USB device list\.$"),
        ("handoff_usb_location_missing", r"^The disk's USB port is not in the Proxmox USB device list\.$"),
        ("handoff_usb_ambiguous", r"^Several USB devices could be this disk\."),
        ("eject_unmount", r"^Unmounting dedicated PBS datastore on PBS\.$"),
        ("eject_unmount_store", r"^Unmounting removable datastore `(?P<store>[^`]+)`"),
        ("eject_already_unmounted", r"^Datastore `(?P<store>[^`]+)` is already unmounted\.$"),
        ("eject_detach", r"^Detaching USB disk from PBS VM\.$"),
        ("spin_down", r"^Disk spin-down/power-off attempted\.$"),
        ("usb_slot_removed", r"^USB passthrough slot `(?P<slot>[^`]+)` removed from the PBS VM\.$"),
        ("target_path_requested", r"^Requested target path: (?P<path>\S+)$"),
        ("target_path_actual", r"^Actual datastore path: (?P<path>\S+)$"),
        ("cancel_requested", r"^Cancellation requested by (?P<user>\S+)\.$"),
    )
)


@dataclass
class LogEntry:
    text: str
    source: str  # "pbo" | "pbs"
    level: str = "info"  # info | ok | warn | err
    at: str | None = None
    step: str | None = None
    code: str | None = None
    params: dict[str, str] = field(default_factory=dict)
    count: int = 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "at": self.at,
            "step": self.step,
            "source": self.source,
            "level": self.level,
            "code": self.code,
            "params": self.params,
            "text": self.text,
            "count": self.count,
        }


def is_progress_line(text: str | None) -> bool:
    """Lines that only report progress: shown as a bar, never as journal lines."""
    value = text or ""
    return bool(_PERCENT_DONE_RE.search(value) or _CHUNKSTORE_RE.search(value))


def _level(text: str, step: str | None, source: str) -> str:
    lowered = text.casefold()
    if step in FAILURE_STEPS or "task error" in lowered or re.search(r"\b(error|failed|failure)\b", lowered) and "retry" not in lowered:
        return "err"
    if "warn" in lowered or "retry" in lowered and "failed" in lowered or "no data changes" in lowered:
        return "warn"
    if "task ok" in lowered or re.search(r"\b(succeeded|completed|reused|ejected|removed|attached)\b", lowered):
        return "ok"
    return "info"


def _pbo_entry(text: str, *, at: str | None, step: str | None) -> LogEntry:
    for code, pattern in PBO_MESSAGES:
        if match := pattern.search(text):
            params = {key: value for key, value in match.groupdict().items() if value is not None}
            return LogEntry(text=text, source="pbo", at=at, step=step, code=code, params=params, level=_level(text, step, "pbo"))
    return LogEntry(text=text, source="pbo", at=at, step=step, level=_level(text, step, "pbo"))


def _split_glued_pbs(text: str) -> tuple[str, list[str]]:
    """'Detaching USB disk from PBS VM. 2026-…+01:00: TASK OK' → PBO part + PBS lines."""
    parts = _PBS_INLINE_RE.split(text)
    return parts[0].strip(), [part.strip() for part in parts[1:] if part.strip()]


def build_entries(stdout_log: str | None, stderr_log: str | None = None) -> list[LogEntry]:
    entries: list[LogEntry] = []
    seen_pbs: set[tuple[str, str]] = set()

    def add(entry: LogEntry) -> None:
        if entry.source == "pbs":
            if is_progress_line(entry.text):
                return
            key = (entry.at or "", entry.text)
            if entry.at and key in seen_pbs:
                return
            seen_pbs.add(key)
        last = entries[-1] if entries else None
        if last and last.text == entry.text and last.source == entry.source:
            last.count += 1
            return
        entries.append(entry)

    def add_pbs_line(raw: str, step: str | None) -> None:
        match = _PBS_LINE_RE.match(raw)
        at, text = (match.group("at"), match.group("text")) if match else (None, raw)
        if text.strip() in {"", "----"}:
            return
        add(LogEntry(text=text.strip(), source="pbs", at=at, step=step, level=_level(text, step, "pbs")))

    for stream, raw_log in (("stdout", stdout_log), ("stderr", stderr_log)):
        current_step: str | None = None
        for raw in (raw_log or "").splitlines():
            line = raw.rstrip()
            if not line.strip():
                continue
            if match := _PBO_ENTRY_RE.match(line):
                current_step = match.group("step")
                body = match.group("text").strip()
                if _PBS_LINE_RE.match(body):  # PBO entry carrying one PBS task line
                    add_pbs_line(body, current_step)
                    continue
                message, glued = _split_glued_pbs(body)
                if message:
                    entry = _pbo_entry(message, at=match.group("at"), step=current_step)
                    if stream == "stderr" and entry.level == "info":
                        entry.level = "err"
                    add(entry)
                for pbs_line in glued:
                    add_pbs_line(pbs_line, current_step)
            elif _PBS_LINE_RE.match(line.strip()):
                add_pbs_line(line.strip(), current_step)
            else:  # detail line of a merged block (PBO text without timestamp)
                add(_pbo_entry(line.strip(), at=None, step=current_step))
    return entries


def _bytes(value: str, unit: str) -> int:
    return int(float(value) * _UNITS[unit])


def transfer_summary(stdout_log: str | None) -> dict[str, Any] | None:
    """Statistics from PBS's `Summary:` line (not from the last archive line)."""
    summary = None
    for line in (stdout_log or "").splitlines():
        if "Summary:" in line:
            summary = line.split("Summary:", 1)[1].strip()
    if summary is None:
        return None
    if "no new data" in summary.casefold():
        return {"text": summary, "transferred_bytes": 0, "rate_bytes_per_second": None, "no_new_data": True}
    size = _SIZE_RE.search(_RATE_RE.sub("", summary))
    rate = _RATE_RE.search(summary)
    return {
        "text": summary,
        "transferred_bytes": _bytes(size.group("n"), size.group("unit")) if size else None,
        "rate_bytes_per_second": _bytes(rate.group("n"), rate.group("unit")) if rate else None,
        "no_new_data": False,
    }


def datastore_pair(run: Any, disk: Any | None) -> dict[str, str | None]:
    """'main → pbo-pbotesta' instead of 'Datastore: main'."""
    target = getattr(disk, "pbs_datastore_name", None) if disk is not None else None
    if not target:
        target_path = (getattr(run, "target_path", None) or "").rstrip("/")
        target = target_path.rsplit("/", 1)[-1] or None
    source = getattr(run, "datastore_name", None) or None
    if source == target:  # eject runs only know the target
        source = None
    return {"source_datastore": source, "target_datastore": target}


def run_kind(run: Any, disk: Any | None) -> str:
    """"eject" for a manual eject run (it only knows the target datastore),
    "backup" otherwise (a backup with auto-eject included, B24)."""
    if getattr(run, "auto_eject_after_success", False):
        return "backup"
    return "eject" if datastore_pair(run, disk)["source_datastore"] is None else "backup"


def run_journal(run: Any, disk: Any | None) -> dict[str, Any]:
    return {
        "run_id": run.id,
        "entries": [entry.as_dict() for entry in build_entries(run.stdout_log, run.stderr_log)],
        "summary": transfer_summary(run.stdout_log),
        **datastore_pair(run, disk),
    }
