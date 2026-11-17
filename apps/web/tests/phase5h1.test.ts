// Phase 5h.1: disk sheet layout helpers, capacity, history, shared percentage
// and usage formatter, eject everywhere.
import assert from "node:assert/strict";
import test from "node:test";

import { translations } from "../src/i18n.ts";
import { overallPercent, resourceRows, runPercent, runSteps, stepStates } from "../src/runs/runModel.ts";
import { formatPercent, usageParts } from "../src/units.ts";
import { capacitySummary, chartWorthShowing, gibScale, historyPage, nextRotationFor, usageSeries } from "../src/views/disks/diskProfile.ts";

const NNBSP = " ";

test("one usage formatter: small values stay readable, never '0 / 491 Gio · 0 %'", () => {
  assert.deepEqual(usageParts(0.18, 491, "fr"), { usage: "184 Mio / 491 Gio", pct: `< 1${NNBSP}%` });
  assert.deepEqual(usageParts(0.18, 491, "en"), { usage: "184 MiB / 491 GiB", pct: "< 1 %" });
  assert.equal(usageParts(0, 491, "en")?.pct, "0 %");
  assert.equal(usageParts(null, 491, "en"), null);
  assert.equal(usageParts(10, 0, "en"), null);
  assert.equal(formatPercent(19.6, "en"), "20 %");
  assert.equal(formatPercent(120, "en"), "100 %");
  assert.equal(formatPercent(null, "en"), "—");
  for (const lang of ["fr", "en"] as const) assert.ok(!translations[lang].disksV2.usage.includes("%"), "the % comes from formatPercent");
});

const run = (extra: Record<string, unknown> = {}) => ({ id: 1, disk_id: 1, status: "running" as const, started_at: "2026-11-18T01:00:00Z", current_step: "run_export", progress_percent: 100, ...extra });

test("one percentage: the dashboard helper equals the modal's weighted value", () => {
  const live = run({ current_step: "cleanup" });
  assert.equal(runPercent(live), overallPercent(live, stepStates(live, []), runSteps(live)));
  assert.ok(runPercent(live) < 100);
  assert.equal(runPercent(run({ status: "success" })), 100);
});

test("run modal: during cleanup / verification / eject no VM is 'in progress'", () => {
  const entries = [{ at: null, step: "sync", source: "pbs" as const, level: "info" as const, code: null, params: {}, text: "sync group vm/100", count: 1 }];
  const copying = resourceRows(run({ current_group: "vm/100" }), entries);
  const cleaning = resourceRows(run({ current_group: "vm/100", current_step: "cleanup" }), entries);
  if (copying.rows.length) {
    assert.equal(copying.rows[0].state, "running");
    assert.equal(cleaning.rows[0].state, "done");
  }
});

const DAY = 24 * 3600 * 1000;
const sample = (day: number, used: number, total = 491) => ({ at: new Date(Date.UTC(2026, 10, day)).toISOString(), used_gb: used, total_gb: total });

test("capacity: growth per backup and 'full in ~N backups' when computable", () => {
  const disk = { capacity_gb: 500, filesystem_used_gb: 300, filesystem_total_gb: 491, usage_history: [sample(1, 100), sample(5, 200), sample(9, 300)] };
  const runs = [3, 5, 7, 9].map((day) => ({ status: "success", started_at: new Date(Date.UTC(2026, 10, day) - 3600_000).toISOString(), finished_at: new Date(Date.UTC(2026, 10, day) - 60_000).toISOString() }));
  const summary = capacitySummary(disk, runs)!;
  assert.equal(summary.freeGb, 191);
  assert.equal(Math.round(summary.growthPerBackupGb!), 50);
  assert.equal(summary.backupsUntilFull, 3);
  // No backups between the samples: no growth, no estimate.
  const none = capacitySummary(disk, [])!;
  assert.equal(none.growthPerBackupGb, null);
  assert.equal(none.backupsUntilFull, null);
  assert.equal(capacitySummary({ capacity_gb: 500, filesystem_used_gb: null, filesystem_total_gb: null }, []), null);
});

test("chart only from 3 measures over 2 days; Y axis in GiB at the data's scale", () => {
  assert.equal(chartWorthShowing(usageSeries([sample(1, 1), sample(2, 2)])), false);
  assert.equal(chartWorthShowing(usageSeries([sample(1, 1), sample(1, 2), sample(2, 3)])), false);
  assert.equal(chartWorthShowing(usageSeries([sample(1, 1), sample(2, 2), sample(3, 3)])), true);
  const low = gibScale(usageSeries([sample(1, 0.15), sample(3, 0.18)]));
  assert.ok(low.max < 1 && low.min >= 0, JSON.stringify(low));
  assert.deepEqual(low.thresholds, []);
  const high = gibScale(usageSeries([sample(1, 380), sample(3, 460)]));
  assert.deepEqual(high.thresholds.map((mark) => mark.pct), [80, 90]);
  assert.ok(high.min > 0 && high.max <= 491 * 1.1);
  assert.ok(DAY > 0);
});

test("history: pages of N, a type filter", () => {
  const items = Array.from({ length: 12 }, (_, index) => ({ id: index, kind: index % 3 === 0 ? "verify" : "backup" }));
  assert.deepEqual(historyPage(items, "all", 10).items.length, 10);
  assert.equal(historyPage(items, "all", 10).more, true);
  assert.equal(historyPage(items, "all", 20).more, false);
  assert.deepEqual(historyPage(items, "verify", 10).items.map((item) => item.id), [0, 3, 6, 9]);
  assert.equal(historyPage(items, "repair", 5).items.length, 0);
});

test("next rotation: the earliest enabled planning naming the disk (rotation included)", () => {
  const disk = { id: 1, serial_number: "PBOTESTA", serial_aliases: [] };
  const events = [
    { enabled: true, disk_serial: "OTHER", rotation_disk_serials: ["OTHER", "PBOTEST-a"], next_occurrence_at: "2026-11-20T03:00:00Z" },
    { enabled: true, disk_serial: "PBOTESTA", next_occurrence_at: "2026-11-25T03:00:00Z" },
    { enabled: false, disk_serial: "PBOTESTA", next_occurrence_at: "2026-11-18T03:00:00Z" },
  ];
  assert.equal(nextRotationFor(disk, events), "2026-11-20T03:00:00Z");
  assert.equal(nextRotationFor(disk, []), null);
});
