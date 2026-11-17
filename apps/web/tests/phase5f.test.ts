// Phase 5f: disk sheet models (colours, kinds, usage series, retention),
// guards (alerts, "To do"), launch state, one-unit usage, run steps.
import assert from "node:assert/strict";
import test from "node:test";

import { translations } from "../src/i18n.ts";
import { reasonText } from "../src/readiness.ts";
import { formatUsage } from "../src/units.ts";
import { todoItems } from "../src/views/dashboard/dashboardModel.ts";
import { activitySummary } from "../src/views/activity/activityModel.ts";
import { runKind } from "../src/views/disks/diskModel.ts";
import {
  alertsFor,
  alertValues,
  chartGeometry,
  DISK_COLORS,
  diskColor,
  diskKind,
  launchState,
  nearestIndex,
  retentionForm,
  retentionPayload,
  todoAlerts,
  usageLevel,
  usageSeries,
} from "../src/views/disks/diskProfile.ts";

const alert = (code: string, level: "warning" | "danger", params: Record<string, unknown> = {}, disk_id = 1) => ({
  disk_id,
  disk_name: "WD 4 To",
  code,
  level,
  params,
});

test("usage in one unit everywhere", () => {
  assert.equal(formatUsage(820, 931, "fr"), "820 / 931 Gio");
  assert.equal(formatUsage(727, 3660, "fr"), "0,71 / 3,57 Tio");
  assert.equal(formatUsage(2048, 4096, "en"), "2 / 4 TiB");
  assert.equal(formatUsage(null, 931, "en"), "—");
});

test("colours and kinds: unknown values fall back", () => {
  assert.equal(DISK_COLORS.length, 6);
  assert.equal(diskColor("violet"), "violet");
  assert.equal(diskColor("pink"), null);
  assert.equal(diskKind("usb_stick"), "usb_stick");
  assert.equal(diskKind(undefined), "unknown");
  for (const kind of ["hdd_portable", "hdd_desktop", "ssd", "usb_stick", "nvme_enclosure", "unknown"] as const) {
    assert.ok(translations.fr.diskSheet.kind[kind] && translations.en.diskSheet.kind[kind]);
  }
});

test("alerts: per disk, ordered by severity; USB 2.0 stays off the 'To do' list", () => {
  const alerts = [alert("usb2_slow", "warning"), alert("rotation_stale", "warning", { days: 40 }), alert("smart_danger", "danger"), alert("usage_warning", "warning", {}, 2)];
  assert.deepEqual(alertsFor(alerts, 1).map((item) => item.code), ["smart_danger", "rotation_stale", "usb2_slow"]);
  assert.deepEqual(todoAlerts(alerts).map((item) => item.code), ["smart_danger", "usage_warning", "rotation_stale"]);
  const values = alertValues(alert("rotation_stale", "warning", { days: 40, last_backup_at: "2026-10-01T00:00:00Z", percent: 93.6 }), () => "1 oct.");
  assert.deepEqual(values, { name: "WD 4 To", days: 40, last_backup_at: "1 oct.", percent: 94 });
  for (const code of ["usage_critical", "usage_warning", "smart_danger", "smart_warning", "verify_recommended", "usb2_slow", "rotation_stale", "pbs_config_recoverable", "pbs_config_missing", "pbs_conflict"]) {
    for (const lang of ["en", "fr"] as const) {
      const guards = translations[lang].guards as Record<string, Record<string, string>>;
      assert.ok(guards.alert[code] && guards.badge[code] && guards.action[code], `${code} (${lang})`);
    }
  }
});

test("dashboard 'To do' lists the disk guards with a link to the disk sheet", () => {
  const items = todoItems({
    disks: [],
    runs: [],
    scheduledRuns: [],
    agentConnected: true,
    agentConfigured: true,
    now: new Date("2026-11-16T11:00:00Z"),
    alerts: [alert("usage_critical", "danger", { percent: 92 }), alert("usb2_slow", "warning")],
  });
  assert.deepEqual(items.map((item) => [item.kind, item.tone, item.href, item.alert?.code]), [["disk_alert", "err", "/disks/1", "usage_critical"]]);
});

test("usage series and chart geometry: one axis in percent", () => {
  const points = usageSeries([
    { at: "2026-11-02T00:00:00Z", used_gb: 900, total_gb: 1000 },
    { at: "2026-11-01T00:00:00Z", used_gb: 500, total_gb: 1000 },
    { at: "2026-11-03T00:00:00Z", used_gb: 10, total_gb: 0 },
  ]);
  assert.deepEqual(points.map((point) => point.percent), [50, 90]);
  const geometry = chartGeometry(points, 100, 50);
  assert.deepEqual(geometry.xs, [0, 100]);
  assert.deepEqual(geometry.ys, [25, 5]);
  assert.ok(geometry.area.endsWith("Z"));
  assert.equal(nearestIndex(geometry.xs, 70), 1);
  assert.deepEqual([usageLevel(79.9), usageLevel(80), usageLevel(95), usageLevel(null)], ["ok", "warning", "danger", "ok"]);
});

test("retention form: defaults to the last 4, at least one whole number", () => {
  assert.deepEqual(retentionForm(null), { keepLast: "4", keepWeekly: "", keepMonthly: "" });
  assert.deepEqual(retentionPayload({ keepLast: "6", keepWeekly: "", keepMonthly: "3" }), { keep_last: 6, keep_weekly: null, keep_monthly: 3 });
  assert.equal(retentionPayload({ keepLast: "", keepWeekly: "", keepMonthly: "" }), null);
  assert.equal(retentionPayload({ keepLast: "0", keepWeekly: "", keepMonthly: "" }), null);
  assert.equal(retentionPayload({ keepLast: "2.5", keepWeekly: "", keepMonthly: "" }), null);
});

test("launch: a failing SMART blocks until explicitly confirmed; other reasons always block", () => {
  const smart = { ready: false, reasons: [{ code: "smart_failed", message: "x" }] };
  assert.deepEqual([launchState(smart, false).canStart, launchState(smart, false).smartFailed], [false, true]);
  assert.equal(launchState(smart, true).canStart, true);
  const space = { ready: false, reasons: [{ code: "smart_failed", message: "x" }, { code: "insufficient_space", message: "y", needed_gib: 300, free_gib: 120.5 }] };
  assert.equal(launchState(space, true).canStart, false);
  assert.equal(launchState(null, true).canStart, false);
  assert.equal(
    reasonText(space.reasons[1], translations.fr.readiness as Record<string, string>, "fr"),
    "Espace insuffisant sur le disque : environ 300 Gio à copier, 120,5 Gio libres. Réduisez la rétention ou utilisez un disque plus grand.",
  );
});

test("verify-only runs and unsafe removals in the history and the activity", () => {
  assert.equal(runKind({ id: 1, disk_id: 1, status: "success", started_at: "", finished_at: null, current_step: "success", progress_percent: null, verify_only: true }), "verify");
  const texts = translations.fr.activityV2.summary;
  const base = { id: "x", at: "2026-11-16T10:00:00Z", duration_seconds: null, disk_id: 1, disk_label: "WD", run_id: 1, planned: false, auto_eject: false, step: null, progress_percent: null, transferred_bytes: null, no_new_data: false, message: null };
  assert.equal(activitySummary({ ...base, type: "verify", status: "success" }, texts, "fr"), "Vérification PBS réussie");
  assert.equal(activitySummary({ ...base, type: "detection", status: "unsafe_removal" }, texts, "fr"), texts.unsafeRemoval);
});

test("PBS configuration deleted: in 'To do' first, linked to the disk sheet, told for a user", () => {
  const items = todoItems({
    disks: [],
    runs: [],
    scheduledRuns: [],
    agentConnected: true,
    agentConfigured: true,
    now: new Date("2026-11-16T11:00:00Z"),
    missingDatastore: "main",
    alerts: [
      alert("rotation_stale", "warning", { days: 40 }),
      alert("pbs_config_recoverable", "warning", { datastore: "pbo-pbotesta" }, 2),
      alert("pbs_config_missing", "danger", { datastore: "pbo-x" }, 3),
      alert("verify_recommended", "warning", {}, 4),
    ],
  });
  assert.deepEqual(
    items.map((item) => [item.kind, item.alert?.code ?? null, item.href]),
    [
      ["datastore_missing", null, "/integrations"],
      ["disk_alert", "pbs_config_missing", "/disks/3"],
      ["disk_alert", "pbs_config_recoverable", "/disks/2"],
      ["disk_alert", "verify_recommended", "/disks/4"],
      ["disk_alert", "rotation_stale", "/disks/1"],
    ],
  );
  for (const lang of ["en", "fr"] as const) {
    const help = translations[lang].datastoresV2.diskHelp;
    for (const note of ["missing_recoverable", "missing", "conflict"] as const) {
      assert.ok(help[note].happened && help[note].data && help[note].next, `${note} (${lang})`);
    }
  }
  assert.match(translations.fr.datastoresV2.diskHelp.missing_recoverable.data, /toujours sur le disque/);
  assert.match(translations.fr.runLog.datastore_recreated, /aucun formatage/);
});
