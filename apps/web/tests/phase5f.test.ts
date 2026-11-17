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
  for (const code of ["usage_critical", "usage_warning", "smart_danger", "smart_warning", "verify_recommended", "usb2_slow", "rotation_stale", "pbs_config_recoverable", "pbs_config_missing", "pbs_conflict", "verify_required", "damaged_versions"]) {
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

// --- Phase 5f.1: token privileges, relative dates ------------------------------------------------

test("relative label: a copy from yesterday evening is 'hier' after midnight (browser time zone)", async () => {
  const { formatWhen } = await import("../src/views/format.ts");
  const texts = translations.fr.dashboardV2;
  const at = new Date(2026, 10, 16, 19, 0); // local 16 Nov 19:00
  const lateEvening = new Date(2026, 10, 16, 23, 30);
  const night = new Date(2026, 10, 17, 3, 36);
  assert.match(formatWhen(at.toISOString(), "fr", lateEvening, texts), /^auj\. 19:00$/);
  assert.match(formatWhen(at.toISOString(), "fr", night, texts), /^hier 19:00$/);
});

test("missing PBS privileges: readable reason with the exact command (FR/EN)", () => {
  const reason = {
    code: "pbs_token_cannot_create_datastore",
    message: "x",
    privileges: "Datastore.Allocate (/datastore)",
    command: "proxmox-backup-manager acl update /datastore Admin --auth-id pbo@pbs",
  };
  const fr = reasonText(reason, translations.fr.readiness as Record<string, string>, "fr");
  assert.ok(fr.startsWith("Le jeton PBO ne peut pas créer de datastore") && fr.endsWith("--auth-id pbo@pbs"));
  for (const lang of ["en", "fr"] as const) {
    for (const code of ["pbs_token_cannot_create_datastore", "pbs_token_missing_privileges", "pbs_permission_denied"]) {
      assert.ok(code in translations[lang].runLog, `${code} runLog (${lang})`);
    }
    assert.ok("pbs_token_missing_privileges" in translations[lang].readiness);
  }
});

// --- Phase 5f.2: removed without eject -----------------------------------------------------------

test("a disk pulled out while handed to PBS: own status, toasts, one-click verification texts", async () => {
  const { diskUiStatus, diskActions } = await import("../src/views/disks/diskModel.ts");
  const { toastForDiskEvent } = await import("../src/liveDisks.ts");
  const disk = { id: 7, serial_number: "PBOTESTA", display_name: "PBOTESTA", capacity_gb: 500, connected: false, trusted: true, status: "pulled" };
  assert.equal(diskUiStatus(disk), "pulled");
  assert.deepEqual(diskActions(disk, "pulled"), ["rescan"]);
  const data = { id: 7, serial_number: "PBOTESTA", display_name: "PBOTESTA", model_name: null, capacity_gb: 500, connected: false, trusted: true, status: "pulled", reject_reason: null, min_size_gb: 32 };
  const pulled = toastForDiskEvent({ id: 1, type: "disk.removed", data } as never, "fr");
  assert.equal(pulled?.tone, "warning");
  assert.match(pulled?.message ?? "", /retiré sans éjection.*vérification/);
  const back = toastForDiskEvent({ id: 2, type: "disk.added", data: { ...data, status: "known", connected: true, verify_recommended: true } } as never, "en");
  assert.match(back?.message ?? "", /plugged back in.*verification/);
  assert.equal(back?.action?.label, "Verify");
  for (const lang of ["en", "fr"] as const) {
    const t = translations[lang];
    assert.ok(t.disksV2.status.pulled && t.disksV2.summary.pulled && t.diskSheet.verify.now && t.diskSheet.verify.failedText);
    assert.ok(t.guards.alert.verify_failed && t.guards.badge.verify_failed && t.guards.action.verify_failed);
    for (const code of ["verify_requested", "disk_pulled_during_sync", "disk_pulled_after_sync", "verify_nothing", "cleanup_gc_failed", "spin_down_unresolved", "released"] as const) assert.ok(t.runLog[code], `${code} (${lang})`);
  }
  assert.equal(translations.fr.runLog.verify_nothing, "Vérification sautée : aucune nouvelle version.");
  assert.match(translations.fr.guards.alert.verify_recommended, /peut-être incomplète.*Rebranchez-le/);
});


// --- Phase 5f.4: durability, damaged versions, repair ------------------------------------------------

test("B47: repair and verification runs have their own steps; the release runs after a failure", async () => {
  const { runSteps, stepStates } = await import("../src/runs/runModel.ts");
  const entry = (step: string, level: "info" | "ok" | "warn" | "err" = "info", code: string | null = null) => ({ at: null, step, source: "pbo" as const, level, code, params: {}, text: step, count: 1 });
  const repair = { id: 1, disk_id: 1, status: "success" as const, started_at: "2026-11-17T04:00:00Z", current_step: "released", progress_percent: 100, repair: true };
  assert.deepEqual(runSteps(repair), ["detect", "passthrough", "mount", "repair", "sync", "cleanup", "verify", "release"]);
  // A failed verification: its step failed, the release still done.
  const failed = { ...repair, repair: false, verify_only: true, status: "failed" as const, current_step: "verify_failed" };
  const entries = [entry("handoff_disk"), entry("prepare_dedicated_datastore"), entry("verify"), entry("verify_failed", "err", "verify_failed"), entry("release"), entry("released", "ok", "released")];
  assert.deepEqual(stepStates(failed, entries), ["done", "done", "done", "failed", "done"]);
  for (const lang of ["en", "fr"] as const) {
    const t = translations[lang];
    assert.ok(t.runModal.kind.repair && t.runModal.steps.repair && t.diskSheet.repair.action && t.diskSheet.verify.requiredTitle);
    for (const code of ["verify_damaged", "verify_damaged_lost", "repair_collateral", "repair_blocked", "repair_done", "datastore_sync_level", "verify_required_first"] as const) {
      assert.ok(t.runLog[code], `${code} (${lang})`);
    }
  }
  assert.match(translations.fr.runLog.disk_pulled_after_sync, /non garantie : vérification obligatoire/);
  assert.doesNotMatch(translations.fr.runLog.disk_pulled_after_sync, /terminée/);
  assert.match(translations.fr.runLog.verify_damaged_lost, /n’existe plus que sous forme abîmée sur ce disque/);
});

// --- Phase 5f.5: guest agent warning, one title per run type ------------------------------------------

test("5f.5: the missing guest agent is a translated, non-blocking warning; each run type has its own lines", () => {
  for (const language of ["en", "fr"] as const) {
    const t = translations[language];
    assert.ok(t.launch.warning.pbs_guest_agent_missing.includes("{vmid}"));
    assert.ok(t.readiness.pbs_guest_agent_missing);
    for (const code of ["run_created_verify", "run_created_repair", "run_starting_verify", "run_starting_repair", "verify_completed", "repair_completed"]) {
      assert.ok((t.runLog as Record<string, string>)[code], `${language} runLog.${code}`);
    }
    assert.notEqual(t.runModal.title.verifyFailed, t.runModal.title.failed);
    assert.notEqual(t.runModal.title.repairFailed, t.runModal.title.failed);
  }
  const reasons = [{ code: "pbs_guest_agent_missing", message: "x", vmid: 100, commands: ["a", "b", "c"] }];
  assert.equal(launchState({ ready: true, reasons: [], warnings: reasons } as never, false).blocking.length, 0);
});
