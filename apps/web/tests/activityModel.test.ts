// Phase 6: activity list (states, one-line summary, duration, filters) and units.
import assert from "node:assert/strict";
import test from "node:test";

import { translations } from "../src/i18n.ts";
import { formatBytes, formatGib } from "../src/units.ts";
import {
  activityDisks,
  activityState,
  activitySummary,
  durationText,
  filterActivity,
  groupActivity,
  type ActivityLike,
} from "../src/views/activity/activityModel.ts";
import { todoItems } from "../src/views/dashboard/dashboardModel.ts";

const NOW = new Date("2026-11-16T12:00:00Z");

function entry(overrides: Partial<ActivityLike> = {}): ActivityLike {
  return {
    id: "run-1",
    type: "backup",
    status: "success",
    at: "2026-11-16T09:00:00Z",
    duration_seconds: 720,
    disk_id: 1,
    disk_label: "WD 4 To",
    run_id: 1,
    planned: false,
    auto_eject: false,
    step: "success",
    progress_percent: 100,
    transferred_bytes: 312 * 1024 ** 3,
    no_new_data: false,
    message: null,
    ...overrides,
  };
}

const FR = translations.fr.activityV2.summary;

test("states of the list", () => {
  assert.equal(activityState(entry()), "ok");
  assert.equal(activityState(entry({ status: "running" })), "running");
  assert.equal(activityState(entry({ status: "failed" })), "failed");
  assert.equal(activityState(entry({ type: "planning", status: "missed" })), "failed");
  assert.equal(activityState(entry({ type: "detection", status: "plugged" })), "info");
});

test("one-line summaries (FR), failure reasons translated from the step code", () => {
  assert.equal(activitySummary(entry(), FR, "fr"), "Sauvegarde réussie · 312 Gio copiés");
  assert.equal(activitySummary(entry({ auto_eject: true }), FR, "fr"), "Sauvegarde réussie · 312 Gio copiés · disque éjecté");
  assert.equal(activitySummary(entry({ no_new_data: true }), FR, "fr"), "Sauvegarde réussie · aucune nouvelle donnée");
  assert.equal(activitySummary(entry({ status: "running", progress_percent: 41.6 }), FR, "fr"), "Sauvegarde en cours · 42 %");
  assert.equal(
    activitySummary(entry({ status: "failed", step: "disk_not_plugged", message: "The disk is no longer plugged" }), FR, "fr", {
      disk_not_plugged: "Disque débranché",
    }),
    "Échec de la sauvegarde : Disque débranché",
  );
  assert.equal(activitySummary(entry({ type: "eject" }), FR, "fr"), "Disque éjecté en toute sécurité");
  assert.equal(activitySummary(entry({ type: "detection", status: "unplugged" }), FR, "fr"), "Disque débranché");
  assert.equal(
    activitySummary(entry({ type: "planning", status: "to_confirm", event_title: "Rotation hebdo" }), FR, "fr"),
    "Sauvegarde planifiée en attente de votre confirmation (Rotation hebdo)",
  );
});

test("durations", () => {
  assert.equal(durationText(42, "fr"), "42 s");
  assert.equal(durationText(720, "fr"), "12 min");
  assert.equal(durationText(3900, "en"), "1 h 05");
  assert.equal(durationText(null, "fr"), null);
});

test("filters: type, state, disk, period and search (accents ignored)", () => {
  const items = [
    entry({ id: "a" }),
    entry({ id: "b", type: "eject", status: "failed", message: "Écriture en cours", at: "2026-11-15T10:00:00Z" }),
    entry({ id: "c", type: "detection", status: "plugged", disk_id: 2, disk_label: "Toshiba", run_id: null, at: "2026-11-01T10:00:00Z" }),
  ];
  const base = { type: "", state: "", diskId: null, days: 30, query: "" } as const;
  assert.deepEqual(filterActivity(items, { ...base }, NOW).map((item) => item.id), ["a", "b", "c"]);
  assert.deepEqual(filterActivity(items, { ...base, type: "eject" }, NOW).map((item) => item.id), ["b"]);
  assert.deepEqual(filterActivity(items, { ...base, state: "failed" }, NOW).map((item) => item.id), ["b"]);
  assert.deepEqual(filterActivity(items, { ...base, diskId: 2 }, NOW).map((item) => item.id), ["c"]);
  assert.deepEqual(filterActivity(items, { ...base, days: 7 }, NOW).map((item) => item.id), ["a", "b"]);
  assert.deepEqual(filterActivity(items, { ...base, query: "ecriture" }, NOW).map((item) => item.id), ["b"]);
  assert.deepEqual(activityDisks(items), [{ id: 2, label: "Toshiba" }, { id: 1, label: "WD 4 To" }]);
});

test("one unit convention: binary sizes (B27)", () => {
  assert.equal(formatGib(931, "fr"), "931 Gio");
  assert.equal(formatGib(3726, "fr"), "3,64 Tio");
  assert.equal(formatGib(0, "en"), "0 B");
  assert.equal(formatBytes(1536, "en"), "1.5 KiB");
  assert.equal(formatBytes(null, "fr"), "—");
});

test("dashboard 'to do': planned runs waiting for a confirmation come first", () => {
  const items = todoItems({
    disks: [],
    runs: [],
    scheduledRuns: [{ id: 9, event_id: 1, event_title: "Rotation hebdo", status: "waiting_for_confirmation", scheduled_for: "2026-11-16T08:00:00Z" }],
    agentConnected: true,
    agentConfigured: true,
    now: NOW,
  });
  assert.deepEqual(items, [{ kind: "to_confirm", tone: "warn", label: "Rotation hebdo", href: "/planning", runId: 9 }]);
});

test("grouping: unplugged then plugged back = one 'replugged' line; identical failures = one line ×N", () => {
  const items = [
    entry({ id: "p", type: "detection", status: "plugged", at: "2026-11-16T10:10:00Z", run_id: null }),
    entry({ id: "u", type: "detection", status: "unplugged", at: "2026-11-16T10:00:00Z", run_id: null }),
    ...[1, 2, 3, 4].map((n) => entry({ id: `f${n}`, status: "failed", step: "disk_not_plugged", at: `2026-11-1${5 - n}T09:00:00Z` })),
    entry({ id: "ok", at: "2026-11-01T09:00:00Z" }),
  ];
  const texts = { disk_not_plugged: "Disque débranché" };
  const summary = (item: ActivityLike) => activitySummary(item, FR, "fr", texts);
  const groups = groupActivity(items, summary);
  assert.deepEqual(
    groups.map((group) => [group.item.id, group.item.status, group.count, group.members.length]),
    [
      ["p", "replugged", 1, 2],
      ["f1", "failed", 4, 4],
      ["ok", "success", 1, 1],
    ],
  );
  assert.equal(summary(groups[0].item), "Disque rebranché");
  // Plugged back much later: two separate lines.
  const late = groupActivity([items[0], { ...items[1], at: "2026-11-15T10:00:00Z" }], summary);
  assert.equal(late.length, 2);
});

test("default filter: backups and ejects only", () => {
  const items = [entry({ id: "b" }), entry({ id: "e", type: "eject" }), entry({ id: "d", type: "detection", status: "plugged" }), entry({ id: "pl", type: "planning", status: "missed" })];
  const base = { type: "runs", state: "", diskId: null, days: 30, query: "" } as const;
  assert.deepEqual(filterActivity(items, base, NOW).map((item) => item.id), ["b", "e"]);
});
