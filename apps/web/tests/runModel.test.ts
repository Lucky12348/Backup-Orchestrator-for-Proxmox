import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

import {
  clockTime,
  entryText,
  filterEntries,
  formatBytes,
  formatRate,
  initialMonitorState,
  isEjectRun,
  journalText,
  monitorReducer,
  resourceRows,
  runStats,
  stepStates,
  type JournalEntry,
  type RunLike,
} from "../src/runs/runModel.ts";
import { translations } from "../src/i18n.ts";

// Journals produced by the API (run_log.py) from the REAL pve-test logs.
const FIXTURES = join(import.meta.dirname, "..", "..", "..", "docs", "design", "fixtures");
function journal(name: string): { entries: JournalEntry[]; summary: { transferred_bytes: number | null; no_new_data: boolean } | null } {
  return JSON.parse(readFileSync(join(FIXTURES, `${name}.journal.json`), "utf8"));
}

function run(overrides: Partial<RunLike> = {}): RunLike {
  return {
    id: 7,
    disk_id: 3,
    status: "running",
    started_at: "2026-11-16T01:39:54",
    current_step: "sync_job",
    progress_percent: 50,
    source_datastore: "main",
    target_datastore: "pbo-pbotesta",
    ...overrides,
  };
}

test("real reuse backup: every step done, resources from the [ct/NNN] lines", () => {
  const { entries } = journal("backup-reuse");
  const finished = run({ status: "success", current_step: "auto_eject", progress_percent: 100, auto_eject_after_success: true });
  assert.deepEqual(stepStates(finished, entries), ["done", "done", "done", "done", "done", "done"]);
  const { rows, pending } = resourceRows(finished, entries);
  assert.deepEqual(rows.map((row) => [row.group, row.state, row.noChanges]), [
    ["ct/101", "done", true],
    ["ct/102", "done", true],
  ]);
  assert.equal(pending, 0);
});

test("running sync: earlier steps done, sync active, time left estimated", () => {
  const { entries } = journal("backup-reuse");
  const syncing = run({ status: "running", current_step: "sync_job", progress_percent: 50, current_group: "ct/102" });
  assert.deepEqual(stepStates(syncing, entries.filter((entry) => entry.step !== "success" && entry.step !== "auto_eject")), [
    "done", "done", "done", "active", "upcoming", "upcoming",
  ]);
  const stats = runStats(syncing, Date.parse("2026-11-16T01:41:54Z"));
  assert.equal(stats.elapsedSeconds, 120);
  assert.equal(stats.remainingSeconds, 120); // 50 % in 2 min → 2 min left
  const rows = resourceRows(syncing, entries).rows;
  assert.deepEqual(rows.map((row) => row.state), ["done", "running"]);
});

test("failure at the USB handoff: passthrough marked failed", () => {
  const entries: JournalEntry[] = journal("backup-reuse").entries.filter((entry) => ["starting", "handoff_disk"].includes(entry.step ?? ""));
  const failed = run({ status: "failed", current_step: "usb_device_not_found", progress_percent: 0 });
  assert.deepEqual(stepStates(failed, entries), ["done", "failed", "upcoming", "upcoming", "upcoming", "upcoming"]);
});

test("real eject run: only the eject step applies", () => {
  const { entries } = journal("eject");
  const eject = run({ status: "success", current_step: "ejected", source_datastore: null });
  assert.equal(isEjectRun(eject, entries), true);
  assert.deepEqual(stepStates(eject, entries), ["skipped", "skipped", "skipped", "skipped", "skipped", "done"]);
});

test("first backup: per-VM sizes and statistics from Summary", () => {
  const { entries, summary } = journal("first-backup-format");
  const rows = resourceRows(run({ status: "success" }), entries).rows;
  assert.equal(rows[0].bytes, Math.round(180.25 * 1024 ** 2));
  assert.equal(formatBytes(summary?.transferred_bytes ?? null, "fr"), "353 Mo"); // ≥ 100: no decimal, as in the mockups
  assert.equal(formatBytes(Math.round(1.5 * 1024 ** 3), "en"), "1.5 GB");
  assert.equal(formatRate("64.208 MiB/s", "fr"), "64,2 Mo/s");
  assert.equal(formatRate("64.208 MiB/s", "en"), "64.2 MB/s");
});

test("log filter, translation and download", () => {
  const { entries } = journal("backup-reuse");
  const warnings = filterEntries(entries, "warn");
  assert.ok(warnings.length > 0 && warnings.every((entry) => entry.level === "warn" || entry.level === "err"));
  assert.equal(filterEntries(entries, "err").length, 0);
  const reused = entries.find((entry) => entry.code === "datastore_reused")!;
  assert.equal(entryText(reused, translations.fr.runLog), "Datastore existant réutilisé, sans formatage.");
  const usb = entries.find((entry) => entry.code === "handoff_usb_chosen")!;
  assert.equal(entryText(usb, translations.en.runLog), "USB device chosen by usb_location: 3-1.");
  const pbs = entries.find((entry) => entry.source === "pbs")!;
  assert.equal(entryText(pbs, translations.fr.runLog), pbs.text); // PBS lines verbatim
  const text = journalText(entries, translations.en.runLog);
  assert.match(text, /OK {3}\[PBS\] TASK OK/);
  assert.ok(!text.includes("percentage done"));
});

test("every PBO code of the real journals is translated in both languages", () => {
  for (const name of ["backup-reuse", "eject", "first-backup-format"]) {
    for (const entry of journal(name).entries) {
      if (!entry.code) continue;
      assert.ok(entry.code in translations.en.runLog, `${entry.code} (en)`);
      assert.ok(entry.code in translations.fr.runLog, `${entry.code} (fr)`);
    }
  }
});

test("clock time of PBO and PBS stamps", () => {
  assert.equal(clockTime("2026-11-16T01:40:00Z"), clockTime("2026-11-16T02:40:00+01:00"));
  assert.equal(clockTime(null), "");
});

// --- SSE: monitor state ------------------------------------------------------------------

test("a run appearing over SSE becomes the active one (pill), without opening", () => {
  const state = monitorReducer(initialMonitorState, {
    type: "event",
    eventType: "run.progress",
    data: { run_id: 12, kind: "external_backup", status: "running", progress_percent: 5, current_step: "handoff_disk" },
  });
  assert.equal(state.activeRunId, 12);
  assert.equal(state.open, false);
  assert.equal(state.runs[12].progress_percent, 5);
});

test("expectRun (eject) opens the modal on the next run event", () => {
  let state = monitorReducer(initialMonitorState, { type: "expect" });
  state = monitorReducer(state, { type: "event", eventType: "run.progress", data: { run_id: 13, kind: "external_backup", status: "running", current_step: "eject" } });
  assert.equal(state.open, true);
  assert.equal(state.activeRunId, 13);
  assert.equal(state.openNext, false);
});

test("minimize keeps the run (pill), finish + close forgets it", () => {
  let state = monitorReducer(initialMonitorState, { type: "open", runId: 14 });
  state = monitorReducer(state, { type: "event", eventType: "run.progress", data: { run_id: 14, kind: "external_backup", status: "running" } });
  state = monitorReducer(state, { type: "minimize" });
  assert.equal(state.open, false);
  assert.equal(state.activeRunId, 14);
  state = monitorReducer(state, { type: "event", eventType: "run.finished", data: { run_id: 14, kind: "external_backup", status: "success" } });
  assert.equal(state.runs[14].status, "success");
  state = monitorReducer(state, { type: "close" });
  assert.equal(state.activeRunId, null);
});

test("disk preparation events never hijack the external backup view", () => {
  const state = monitorReducer(initialMonitorState, { type: "event", eventType: "run.progress", data: { run_id: 3, kind: "disk_preparation", status: "running" } });
  assert.equal(state, initialMonitorState);
});
