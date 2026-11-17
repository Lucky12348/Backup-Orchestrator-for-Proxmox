// Phase 5h (UI only): disk identity everywhere, toasts, context menus, run
// modal progress and journal, disk sheet dates and sizes, dashboard state.
import assert from "node:assert/strict";
import test from "node:test";

import { translations } from "../src/i18n.ts";
import { currentActivity, isAtBottom, overallPercent, REPAIR_STEPS, RUN_STEPS, VERIFY_STEPS } from "../src/runs/runModel.ts";
import { isMenuKey, menuPosition, nextIndex } from "../src/ui/actionMenuModel.ts";
import { addToast, DURATIONS, makeToast, MAX_TOASTS, remainingAfter } from "../src/ui/toastStore.ts";
import { formatGib, formatUsage } from "../src/units.ts";
import { dashboardState } from "../src/views/dashboard/dashboardModel.ts";
import { formatReadAt, formatWhen, serverDate } from "../src/views/format.ts";
import { diskSecondary, findDisk } from "../src/views/disks/diskProfile.ts";
import { pveGuestUrl } from "../src/views/resources/resourcesModel.ts";

const entry = (step: string, text = step, source: "pbo" | "pbs" = "pbo") => ({ at: null, step, source, level: "info" as const, code: null, params: {}, text, count: 1 });

// --- 1. identity ---------------------------------------------------------------------------------

test("a disk is found by id or by any of its serials; model and serial are secondary", () => {
  const disks = [
    { id: 1, serial_number: "PBOTESTA", canonical_serial_number: "PBOTESTA", serial_aliases: ["PBOTEST-a"], display_name: "Rotation A", model_name: "QEMU HARDDISK" },
    { id: 2, serial_number: "WD-123", display_name: "Bureau", model_name: null },
  ];
  assert.equal(findDisk(disks, { id: 2 })?.display_name, "Bureau");
  assert.equal(findDisk(disks, { serial: "pbotest-a" })?.id, 1);
  assert.equal(findDisk(disks, { serial: "nope" }), undefined);
  assert.equal(diskSecondary(disks[0]), "QEMU HARDDISK · PBOTESTA");
  assert.equal(diskSecondary({ display_name: "WD-123", model_name: null, serial_number: "WD-123" }), "");
});

// --- 2. toasts -----------------------------------------------------------------------------------

test("toasts: 8 s / 12 s / until closed, three at most, errors kept, pause on hover", () => {
  assert.deepEqual([DURATIONS.info, DURATIONS.success, DURATIONS.warning, DURATIONS.danger], [8000, 8000, 12000, 0]);
  assert.equal(MAX_TOASTS, 3);
  let list = addToast([], makeToast({ id: "err", title: "E", tone: "danger" }));
  for (const id of ["a", "b", "c"]) list = addToast(list, makeToast({ id, title: id }));
  assert.deepEqual(list.map((toast) => toast.id), ["err", "b", "c"]); // "a" went, the error stays
  assert.equal(remainingAfter(8000, 1000, 4000), 5000);
  assert.equal(remainingAfter(8000, 0, 9000), 0);
  assert.equal(translations.fr.shell.view, "Voir");
});

// --- 3. context menus ----------------------------------------------------------------------------

test("context menus: Shift+F10 / Menu key, kept in the viewport, arrows skip disabled entries", () => {
  assert.equal(isMenuKey({ key: "F10", shiftKey: true }), true);
  assert.equal(isMenuKey({ key: "ContextMenu", shiftKey: false }), true);
  assert.equal(isMenuKey({ key: "F10", shiftKey: false }), false);
  assert.deepEqual(menuPosition({ x: 1000, y: 700 }, { width: 240, height: 200 }, { width: 1100, height: 800 }), { left: 760, top: 500 });
  assert.deepEqual(menuPosition({ x: 2, y: 3 }, { width: 240, height: 200 }, { width: 390, height: 800 }), { left: 8, top: 8 });
  assert.equal(nextIndex(0, "ArrowDown", [false, true, false]), 2);
  assert.equal(nextIndex(2, "ArrowDown", [false, true, false]), 0);
  assert.equal(nextIndex(0, "ArrowUp", [false, true, false]), 2);
  assert.equal(nextIndex(-1, "Home", [true, false]), 1);
  for (const lang of ["fr", "en"] as const) {
    const menu = translations[lang].menu;
    for (const key of ["backup", "verify", "eject", "open", "rename", "copySerial"] as const) assert.ok(menu.disk[key], `${lang} disk.${key}`);
    for (const key of ["runNow", "edit", "duplicate", "disable", "enable", "delete"] as const) assert.ok(menu.event[key], `${lang} event.${key}`);
    for (const key of ["copies", "openPve", "protect", "unprotect"] as const) assert.ok(menu.resource[key], `${lang} resource.${key}`);
    assert.ok(menu.run.open && menu.run.download && menu.run.relaunch.backup && menu.job.runNowHint);
  }
});

test("Open in Proxmox: the guest's page of the PVE web UI", () => {
  assert.equal(pveGuestUrl("https://pve.lan:8006/api2/json", { vm_type: "vm", external_id: "100" }), "https://pve.lan:8006/#v1:0:=qemu%2F100");
  assert.equal(pveGuestUrl("https://10.0.0.1:8006", { vm_type: "ct", external_id: "101" }), "https://10.0.0.1:8006/#v1:0:=lxc%2F101");
  assert.equal(pveGuestUrl(null, { vm_type: "vm", external_id: "100" }), null);
  assert.equal(pveGuestUrl("not a url", { vm_type: "vm", external_id: "100" }), null);
});

// --- 4. run modal --------------------------------------------------------------------------------

const run = (extra: Record<string, unknown> = {}) => ({ id: 1, disk_id: 1, status: "running" as const, started_at: "2026-11-18T01:00:00Z", current_step: "run_export", progress_percent: 100, ...extra });

test("B35: one weighted percentage for the whole run, never 100 % before its end", () => {
  // The copy is at 100 %, but cleanup, verification and eject are still ahead.
  const states = ["done", "done", "done", "active", "upcoming", "upcoming", "upcoming"] as const;
  const percent = overallPercent(run(), [...states], RUN_STEPS);
  assert.ok(percent > 60 && percent < 80, String(percent));
  const half = overallPercent(run({ progress_percent: 50 }), [...states], RUN_STEPS);
  assert.ok(half < percent && half > 15);
  const last = overallPercent(run({ current_step: "eject" }), ["done", "done", "done", "done", "done", "done", "active"], RUN_STEPS);
  assert.ok(last < 100 && last >= 90);
  assert.equal(overallPercent(run({ status: "success" }), ["done", "done", "done", "done", "done", "done", "done"], RUN_STEPS), 100);
  // Skipped steps do not count (no verification asked).
  const skipped = overallPercent(run({ current_step: "cleanup" }), ["done", "done", "done", "done", "active", "skipped", "upcoming"], RUN_STEPS);
  assert.ok(skipped > 70 && skipped < 99);
  // A verification run: its own steps.
  assert.ok(overallPercent(run({ verify_only: true, current_step: "verify" }), ["done", "done", "done", "active", "upcoming"], VERIFY_STEPS) < 60);
});

test("B36: the journal follows only while the reader is at the bottom", () => {
  assert.equal(isAtBottom(980, 200, 1200), true);
  assert.equal(isAtBottom(900, 200, 1200), false);
});

test("the left panel says what a verification or a repair is doing", () => {
  const entries = [entry("handoff_disk"), entry("verify", "Verifying the copy."), entry("verify", "verify pbo-a:ct/101/…", "pbs")];
  const activity = currentActivity(VERIFY_STEPS, ["done", "done", "done", "active", "upcoming"], entries);
  assert.equal(activity.step, "verify");
  assert.deepEqual(activity.lines.map((line) => line.text), ["Verifying the copy.", "verify pbo-a:ct/101/…"]);
  assert.equal(currentActivity(REPAIR_STEPS, ["done", "done", "done", "active", "upcoming", "upcoming", "upcoming", "upcoming"], [entry("repair")]).step, "repair");
  for (const lang of ["fr", "en"] as const) assert.ok(translations[lang].runModal.resumeFollow && translations[lang].runModal.nowDoing);
});

// --- 5. disk sheet -------------------------------------------------------------------------------

test("B38: server times without a zone are UTC, shown at the local time", () => {
  assert.equal(serverDate("2026-11-17T03:10:00").toISOString(), "2026-11-17T03:10:00.000Z");
  assert.equal(serverDate("2026-11-17T03:10:00+01:00").toISOString(), "2026-11-17T02:10:00.000Z");
  const now = new Date("2026-11-17T12:00:00Z");
  const local = new Date("2026-11-17T03:10:00Z");
  const hh = String(local.getHours()).padStart(2, "0");
  const mm = String(local.getMinutes()).padStart(2, "0");
  assert.equal(formatReadAt("2026-11-17T03:10:00", "fr", now, translations.fr.diskSheet), `Lu aujourd’hui à ${hh}:${mm}`);
  assert.ok(formatReadAt("2026-11-01T03:10:00", "en", now, translations.en.diskSheet).startsWith("Read on 1 Nov at"));
  assert.ok(formatWhen("2026-11-17T03:10:00", "fr", now, translations.fr.dashboardV2).includes(`${hh}:${mm}`));
});

test("B38: small sizes stay readable, never 0 / 491 GiB", () => {
  assert.equal(formatGib(0.18, "fr"), "184 Mio");
  assert.equal(formatGib(2.345, "en"), "2.35 GiB");
  assert.equal(formatGib(931, "fr"), "931 Gio");
  assert.equal(formatUsage(0.18, 491, "fr"), "0,18 / 491 Gio");
  assert.equal(formatUsage(0.001, 491, "fr"), "1 Mio / 491 Gio");
  assert.equal(formatUsage(0, 491, "fr"), "0 / 491 Gio");
  assert.equal(formatUsage(820, 931, "fr"), "820 / 931 Gio");
});

// --- 6. dashboard --------------------------------------------------------------------------------

test("B42: never 'all protected' in green while something waits in To do", () => {
  assert.equal(dashboardState({ state: "protected" }, 0), "protected");
  assert.equal(dashboardState({ state: "protected" }, 2), "attention");
  assert.equal(dashboardState({ state: "at_risk" }, 2), "at_risk");
  assert.equal(dashboardState({ state: "empty" }, 1), "empty");
  for (const lang of ["fr", "en"] as const) assert.ok(translations[lang].dashboardV2.attention && translations[lang].dashboardV2.attentionText.includes("{n}"));
});
