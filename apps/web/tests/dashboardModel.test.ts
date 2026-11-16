// Phase 5b: dashboard model (protection, next rotation, to do, rotation disks,
// last runs) and the date / size formats of the mockups.
import assert from "node:assert/strict";
import test from "node:test";

import {
  currentOrLastRun,
  integrations,
  nextRotation,
  protectionSummary,
  recentRuns,
  rotationDisks,
  todoItems,
  type DashboardRun,
} from "../src/views/dashboard/dashboardModel.ts";
import type { DiskLike } from "../src/views/disks/diskModel.ts";
import { fill, formatSize, formatWhen } from "../src/views/format.ts";

const NOW = new Date("2026-11-16T12:00:00Z");

const disks: DiskLike[] = [
  { id: 1, serial_number: "WXD2DA1L1E7C", display_name: "WD 4 To", capacity_gb: 4000, connected: true, trusted: true, filesystem_total_gb: 3660, filesystem_used_gb: 727 },
  { id: 2, serial_number: "Z87CTEM3T", display_name: "Toshiba 3 To", capacity_gb: 3000, connected: false, trusted: true, status: "unplugged" },
  { id: 3, serial_number: "SANDISK1", display_name: "SanDisk", capacity_gb: 1000, connected: true, trusted: false, status: "new" },
];

function run(id: number, overrides: Partial<DashboardRun> = {}): DashboardRun {
  return {
    id,
    disk_id: 1,
    disk_name: "WD 4 To",
    status: "success",
    started_at: `2026-11-${String(id).padStart(2, "0")}T09:00:00Z`,
    finished_at: `2026-11-${String(id).padStart(2, "0")}T10:00:00Z`,
    current_step: "success",
    progress_percent: 100,
    ...overrides,
  };
}

test("protection: everything protected, at risk, nothing to protect", () => {
  assert.deepEqual(protectionSummary({ total_vms: 11, protected_vms: 11, ignored_vms: 2, coverage_percent: 100 }), {
    state: "protected",
    percent: 100,
    total: 11,
    protectedCount: 11,
    atRisk: 0,
    ignored: 2,
  });
  const risk = protectionSummary({ total_vms: 10, protected_vms: 7, ignored_vms: 0, coverage_percent: 70 });
  assert.equal(risk.state, "at_risk");
  assert.equal(risk.atRisk, 3);
  assert.equal(protectionSummary({ total_vms: 0, protected_vms: 0, ignored_vms: 0, coverage_percent: 0 }).state, "empty");
});

test("current task first, else the last run", () => {
  const runs = [run(14), run(15, { status: "running", finished_at: null }), run(13, { status: "failed" })];
  assert.deepEqual(currentOrLastRun(runs), { run: runs[1], active: true });
  assert.deepEqual(currentOrLastRun([run(14), run(13)]), { run: run(14), active: false });
  assert.equal(currentOrLastRun([]), null);
});

test("next rotation: the earliest enabled occurrence, with its disk plugged or not", () => {
  const events = [
    { id: 1, title: "Weekly", enabled: true, disk_serial: "z87-ctem3t", disk_label_or_model: null, next_occurrence_at: "2026-11-22T08:00:00Z" },
    { id: 2, title: "Disabled", enabled: false, disk_serial: "WXD2DA1L1E7C", disk_label_or_model: null, next_occurrence_at: "2026-11-17T08:00:00Z" },
    { id: 3, title: "Past", enabled: true, disk_serial: "WXD2DA1L1E7C", disk_label_or_model: null, next_occurrence_at: "2026-11-10T08:00:00Z" },
  ];
  assert.deepEqual(nextRotation(events, disks, NOW), {
    at: "2026-11-22T08:00:00Z",
    title: "Weekly",
    diskLabel: "Toshiba 3 To",
    diskId: 2,
    diskPlugged: false,
  });
  assert.equal(nextRotation([], disks, NOW), null);
});

test("to do: only what needs an action", () => {
  const items = todoItems({
    disks,
    runs: [run(16, { status: "failed", started_at: "2026-11-16T09:00:00Z", finished_at: "2026-11-16T09:10:00Z" })],
    scheduledRuns: [
      { id: 1, event_id: 1, event_title: "Weekly", status: "missed", scheduled_for: "2026-11-15T08:00:00Z" },
      { id: 2, event_id: 1, event_title: "Old", status: "missed", scheduled_for: "2026-10-01T08:00:00Z" },
    ],
    agentConnected: false,
    agentConfigured: true,
    now: NOW,
  });
  assert.deepEqual(
    items.map((item) => [item.kind, item.label]),
    [
      ["new_disk", "SanDisk"],
      ["missed_rotation", "Weekly"],
      ["agent_offline", ""],
      ["recent_failure", "WD 4 To"],
    ],
  );
  const calm = todoItems({ disks: disks.slice(0, 2), runs: [run(15)], scheduledRuns: [], agentConnected: true, agentConfigured: true, now: NOW });
  assert.deepEqual(calm, []);
});

test("rotation disks: approved ones, the running one first, usage and last backup", () => {
  const list = rotationDisks(disks, [run(15, { disk_id: 2 }), run(16, { status: "running", finished_at: null, progress_percent: 42 })]);
  assert.deepEqual(
    list.map((item) => [item.name, item.status, item.percent]),
    [
      ["WD 4 To", "running", 42],
      ["Toshiba 3 To", "unplugged", null],
    ],
  );
  assert.equal(list[0].usage, 20);
  assert.equal(list[0].freeGb, 2933);
  assert.equal(list[1].lastBackupAt, "2026-11-15T10:00:00Z");
});

test("last runs: 5 at most, newest first, with their tone", () => {
  const runs = [1, 2, 3, 4, 5, 6, 7].map((id) => run(id, id === 7 ? { status: "running" } : id === 6 ? { status: "failed" } : {}));
  const recent = recentRuns(runs);
  assert.equal(recent.length, 5);
  assert.deepEqual(recent.map((item) => [item.run.id, item.tone]).slice(0, 3), [
    [7, "run"],
    [6, "err"],
    [5, "ok"],
  ]);
});

test("integrations footer", () => {
  assert.equal(integrations({ pve: true, pbs: true, agent: true }).allOk, true);
  assert.equal(integrations({ pve: true, pbs: false, agent: true }).allOk, false);
});

test("formats of the mockups", () => {
  const texts = { today: "auj. {time}", yesterday: "hier {time}", never: "jamais" };
  const local = new Date(2026, 10, 16, 14, 2).toISOString();
  assert.equal(formatWhen(local, "fr", new Date(2026, 10, 16, 18, 0), texts), "auj. 14:02");
  assert.equal(formatWhen(null, "fr", NOW, texts), "jamais");
  assert.match(formatWhen(new Date(2026, 10, 8, 9, 0).toISOString(), "fr", new Date(2026, 10, 16), texts), /^8 nov/);
  assert.equal(formatSize(3660, "fr"), "3,57 Tio");
  assert.equal(formatSize(931, "en"), "931 GiB");
  assert.equal(fill("{n} ressources ignorées", { n: 2 }), "2 ressources ignorées");
});
